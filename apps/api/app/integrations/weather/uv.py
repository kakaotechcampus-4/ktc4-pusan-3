"""기상청 생활기상지수 자외선(`LivingWthrIdxServiceV5/getUVIdxV5`) — 시군구 하나의 한 칸.

하는 일 (설계 D8)
  1. 격자에서 시군구를 정한다(`areas.nearest_district`). 밖으로는 시군구 코드만 나간다 — API 는
     읍면동 코드도 받지만 더 잘게 위치를 드러내서 쓰지 않는다. 자외선은 시군구 안에서 거의 같다
  2. 그 시군구의 최근 발표를 받아 (시군구, 지금 시각의 시)로 캐시한다
  3. 발표 시각(`date`)부터 3시간 간격 칸(`h0` · `h3` · … `h75`) 중 그날 볼 칸의 지수 원문을 준다.
     오늘은 지금이 든 칸, 다른 날은 12시가 든 칸이다(단기예보와 같은 `DAYTIME_HOUR`)

먼 칸은 빈 문자열로 온다. 그대로 넘긴다 — "낮음"이 아니라 "확인 못 함"으로 읽는 것은
Activity `weather.py` 다.
"""

import asyncio
import logging
from collections import OrderedDict
from collections.abc import Callable
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from app.core.weather_raw import UpstreamUnavailable, WeatherGrid
from app.integrations.weather.areas import nearest_district
from app.integrations.weather.forecast import DAYTIME_HOUR
from app.integrations.weather.public_data import get_json, result_code

log = logging.getLogger(__name__)

URL = "https://apis.data.go.kr/1360000/LivingWthrIdxServiceV5/getUVIdxV5"
KST = ZoneInfo("Asia/Seoul")

SLOT_HOURS = 3
LAST_SLOT = 75  # h75 — 발표 뒤 75시간까지 칸이 있다

CACHE_SIZE = 512
_OK = "00"

UvRow = dict[str, Any]


def slot_key(issued: datetime, target: datetime) -> str | None:
    """발표 시각에서 `target` 이 든 칸 이름(`h0` · `h3` …). 발표 전이거나 범위 밖이면 None."""
    hours = (target - issued) // timedelta(hours=1)
    if hours < 0:
        hours = 0  # 발표 시각 직전 몇 분 — 첫 칸을 쓴다
    slot = hours // SLOT_HOURS * SLOT_HOURS
    return f"h{slot}" if slot <= LAST_SLOT else None


def parse_uv(payload: dict[str, Any]) -> UvRow:
    code = result_code(payload)
    if code != _OK:
        raise UpstreamUnavailable(f"자외선 결과 코드 {code}")
    items = ((payload["response"].get("body") or {}).get("items") or {}).get("item") or []
    if not items:
        raise UpstreamUnavailable("자외선 응답에 행이 없다")
    return items[0]


def issued_at(row: UvRow) -> datetime:
    """`date`(YYYYMMDDHH) — 발표 시각. 못 읽으면 실패다."""
    try:
        return datetime.strptime(str(row["date"]), "%Y%m%d%H").replace(tzinfo=KST)
    except (KeyError, ValueError):
        raise UpstreamUnavailable("자외선 발표 시각을 읽지 못했다") from None


class KmaUv:
    """자외선 어댑터. Activity `WeatherSource.uv` 와 같은 모양이다. 프로세스에 하나를 둔다."""

    def __init__(
        self,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._client = httpx.AsyncClient(transport=transport)
        self._now = now or (lambda: datetime.now(KST))
        self._cache: OrderedDict[tuple[str, str], UvRow] = OrderedDict()
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}

    async def aclose(self) -> None:
        await self._client.aclose()

    async def uv(self, *, grid: WeatherGrid, day: date) -> str | None:
        """그날 볼 칸의 지수 원문. 칸이 없으면 None, 조회가 실패하면 UpstreamUnavailable."""
        district = nearest_district(grid)
        if district is None:
            raise UpstreamUnavailable("격자에 맞는 시군구가 없다")
        now = self._now().astimezone(KST)
        row = await self._row(district, now)
        target = now if day == now.date() else datetime.combine(day, time(DAYTIME_HOUR), KST)
        key = slot_key(issued_at(row), target)
        if key is None or row.get(key) is None:
            return None
        return str(row[key])

    async def _row(self, district: str, now: datetime) -> UvRow:
        key = (district, now.strftime("%Y%m%d%H"))
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            if key in self._cache:
                return self._cache[key]
            payload = await get_json(
                self._client,
                URL,
                {
                    "pageNo": 1,
                    "numOfRows": 10,
                    "dataType": "JSON",
                    "areaNo": district,
                    "time": key[1],
                },
                what="자외선",
            )
            row = parse_uv(payload)
            self._cache[key] = row
            if len(self._cache) > CACHE_SIZE:
                self._cache.popitem(last=False)
        self._locks.pop(key, None)
        return row
