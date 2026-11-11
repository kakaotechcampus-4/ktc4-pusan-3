"""기상청 중기 육상예보(`MidFcstInfoService/getMidLandFcst`) — 단기예보 범위(약 3일) 밖의 날.

하는 일
  1. 격자를 중기 육상예보 구역(`kma_mid_regions.region_of`)으로 바꾼다 — 좌표 · 격자는 보내지 않고
     광역 구역 코드만 보낸다
  2. 지금 받을 수 있는 가장 최근 발표(06 · 18시)를 구역마다 한 번 받아 캐시한다
  3. 그날(발표일에서 4 ~ 10일 뒤)의 강수확률 · 하늘 문장을 꺼낸다 — 7일 뒤까지는 오전 · 오후가
     따로 와서 강수확률은 큰 값, 하늘은 비 · 눈이 든 쪽(없으면 오후)이다

해석(비 · 눈 문장 → 바깥 판정)은 하지 않는다 — Activity `weather.py` 가 한다.
"""

import logging
from collections.abc import Callable
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from app.core.kma_mid_regions import region_of
from app.core.weather_raw import RawForecast, UpstreamUnavailable, WeatherGrid
from app.integrations.weather.inflight import Inflight
from app.integrations.weather.public_data import get_json, result_code

log = logging.getLogger(__name__)

URL = "https://apis.data.go.kr/1360000/MidFcstInfoService/getMidLandFcst"
KST = ZoneInfo("Asia/Seoul")

# 발표 시각(KST). 발표 뒤 올라오기까지 넉넉히 30분을 둔다 (잠정)
TM_HOURS = (6, 18)
PUBLISH_DELAY = timedelta(minutes=30)
# 발표일에서 며칠 뒤까지 오는가. 7일 뒤까지는 오전 · 오후 칸이다
FIRST_DAY = 4
HALF_DAY_LAST = 7
LAST_DAY = 10
_RAINY = ("비", "눈", "소나기")
_OK = "00"


def latest_tm(now: datetime) -> tuple[str, date]:
    """지금 받을 수 있는 가장 최근 발표 (tmFc, 발표일)."""
    local = now.astimezone(KST)
    for days_back in (0, 1):
        day = local.date() - timedelta(days=days_back)
        for hour in reversed(TM_HOURS):
            issued = datetime.combine(day, time(hour), tzinfo=KST)
            if issued + PUBLISH_DELAY <= local:
                return issued.strftime("%Y%m%d%H00"), day
    raise AssertionError("이틀 안에 발표가 없을 수 없다")


class KmaMidForecast:
    """중기 육상예보 어댑터. `PublicWeather` 가 단기예보에 그날 칸이 없을 때 부른다."""

    def __init__(
        self,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._client = httpx.AsyncClient(transport=transport)
        self._now = now or (lambda: datetime.now(KST))
        self._cache: dict[tuple[str, str], dict[str, Any]] = {}
        # 받는 중인 조회. 3일 이상 뒤의 주말(토 · 일)은 같은 (구역, 발표)를 동시에 부른다 (#962)
        self._inflight: Inflight[tuple[str, str], dict[str, Any]] = Inflight()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def forecast(self, *, grid: WeatherGrid, day: date) -> RawForecast:
        """그날의 강수확률 · 하늘 문장. 범위 밖이거나 칸이 없거나 조회가 실패하면
        UpstreamUnavailable — Activity 는 "날씨 확인 못 함"으로 본다."""
        tm, issued_on = latest_tm(self._now())
        offset = (day - issued_on).days
        if not FIRST_DAY <= offset <= LAST_DAY:
            raise UpstreamUnavailable("중기예보 범위 밖의 날이다")
        item = await self._item(region_of(grid), tm)
        if offset <= HALF_DAY_LAST:
            pops = [item.get(f"rnSt{offset}Am"), item.get(f"rnSt{offset}Pm")]
            skies = [_text(item.get(f"wf{offset}Am")), _text(item.get(f"wf{offset}Pm"))]
        else:
            pops = [item.get(f"rnSt{offset}")]
            skies = [_text(item.get(f"wf{offset}"))]
        numbers = [
            int(value)
            for value in pops
            if isinstance(value, int | float | str) and str(value).isdigit()
        ]
        known = [sky for sky in skies if sky]
        if not numbers or not known:
            raise UpstreamUnavailable("중기예보에 그날 칸이 없다")
        rainy = [sky for sky in known if any(word in sky for word in _RAINY)]
        return RawForecast(sky=None, pcp=None, pop=str(max(numbers)), wf=(rainy or known)[-1])

    async def _item(self, region: str, tm: str) -> dict[str, Any]:
        key = (region, tm)
        if key in self._cache:
            return self._cache[key]
        return await self._inflight.run(key, lambda: self._fetch(key))

    async def _fetch(self, key: tuple[str, str]) -> dict[str, Any]:
        region, tm = key
        payload = await get_json(
            self._client,
            URL,
            {"pageNo": 1, "numOfRows": 10, "dataType": "JSON", "regId": region, "tmFc": tm},
            what="중기예보",
        )
        code = result_code(payload)
        if code != _OK:
            raise UpstreamUnavailable(f"중기예보 결과 코드 {code}")
        body = payload["response"].get("body") or {}
        items = (body.get("items") or {}).get("item") or []
        if not items or not isinstance(items[0], dict):
            raise UpstreamUnavailable("중기예보 행이 없다")
        # 발표가 바뀌면 옛 발표는 버린다 — 구역 10곳 × 발표 1개만 둔다
        self._cache = {k: v for k, v in self._cache.items() if k[1] == tm}
        self._cache[key] = items[0]
        return items[0]


def _text(value: object) -> str:
    return "" if value is None else str(value).strip()
