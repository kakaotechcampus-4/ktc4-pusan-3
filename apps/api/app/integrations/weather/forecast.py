"""기상청 단기예보(`VilageFcstInfoService_2.0/getVilageFcst`) — 격자 하나의 한 시각 칸.

하는 일은 셋뿐이다 (설계 D8).
  1. 지금 받을 수 있는 가장 최근 발표 시각을 고른다 (`latest_base`)
  2. 그 발표분을 격자 단위로 받아 캐시한다 — 키는 시계가 아니라 (nx, ny, 발표일, 발표시각)이라
     같은 발표분을 두 번 부르지 않고, 새 발표가 나오면 저절로 바뀐다 (D9)
  3. 그날의 한 시각 칸(`slot_for`)에서 SKY · PCP · POP 원문을 꺼낸다

해석("강수없음" · "1.0mm 미만" · 구간 문자열)은 하지 않는다 — Activity `weather.py` 가 한다.
"""

import asyncio
import logging
from collections import OrderedDict
from collections.abc import Callable
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from app.core.weather_raw import RawForecast, UpstreamUnavailable, WeatherGrid
from app.integrations.weather.public_data import get_json, kma_result_code

log = logging.getLogger(__name__)

URL = "https://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getVilageFcst"
KST = ZoneInfo("Asia/Seoul")

# 발표 시각(KST). 발표 뒤 API 에 올라오기까지 약 10분 걸린다 (단기예보 활용가이드)
BASE_HOURS = (2, 5, 8, 11, 14, 17, 20, 23)
PUBLISH_DELAY = timedelta(minutes=10)

# 오늘이 아닌 날은 이 시각 칸 하나를 본다 — 놀이 시간대(08–20시)의 한가운데 (잠정)
DAYTIME_HOUR = 12

# 한 발표분은 격자 하나에 약 1,000행(12종 × 시간 × 3–4일)이다. 쪽을 나누면 호출이 두 번이라
# 한 번에 받는다
ROWS_PER_CALL = 1500

# 캐시에 두는 발표분 수. 격자 하나 · 발표 하나가 한 칸이다
CACHE_SIZE = 256

_CATEGORIES = frozenset({"SKY", "PCP", "POP"})
_OK = "00"

Slot = tuple[str, str]  # (fcstDate YYYYMMDD, fcstTime HHMM)
Slots = dict[Slot, dict[str, str]]


def latest_base(now: datetime) -> tuple[str, str]:
    """지금 받을 수 있는 가장 최근 발표 (base_date, base_time). `now` 는 KST 로 바꿔 본다."""
    local = now.astimezone(KST)
    for days_back in (0, 1):
        day = local.date() - timedelta(days=days_back)
        for hour in reversed(BASE_HOURS):
            issued = datetime.combine(day, time(hour), tzinfo=KST)
            if issued + PUBLISH_DELAY <= local:
                return issued.strftime("%Y%m%d"), issued.strftime("%H00")
    raise AssertionError("이틀 안에 발표가 없을 수 없다")  # 23시 발표가 하루 전에 늘 있다


def slot_for(day: date, now: datetime, slots: Slots) -> Slot | None:
    """그날 볼 칸 하나.

    - 오늘이면 지금 시각 이후 첫 칸이다 — 이미 지난 시각의 예보로 판정하지 않는다.
    - 다른 날이면 `DAYTIME_HOUR` 칸이다.
    - 발표분에 그 칸이 없으면(예보 범위 밖 · 오늘 마지막 칸이 지남) None.
    """
    local = now.astimezone(KST)
    target_date = day.strftime("%Y%m%d")
    if day == local.date():
        start = f"{local.hour:02d}00"
        times = sorted(t for d, t in slots if d == target_date and t >= start)
        return (target_date, times[0]) if times else None
    slot = (target_date, f"{DAYTIME_HOUR:02d}00")
    return slot if slot in slots else None


def parse_slots(payload: dict[str, Any]) -> Slots:
    """응답 행을 (예보일, 예보시각) → {SKY · PCP · POP: 원문} 으로. 나머지 종류는 버린다."""
    code = kma_result_code(payload)
    if code != _OK:
        # NO_DATA 도 여기서는 실패다 — 특보와 달리 예보가 비는 정상 상황이 없다
        raise UpstreamUnavailable(f"단기예보 결과 코드 {code}")
    body = payload["response"].get("body") or {}
    items = ((body.get("items") or {}).get("item")) or []
    slots: Slots = {}
    for item in items:
        category = item.get("category")
        if category not in _CATEGORIES:
            continue
        key = (str(item.get("fcstDate")), str(item.get("fcstTime")))
        value = item.get("fcstValue")
        slots.setdefault(key, {})[category] = None if value is None else str(value)
    return slots


class KmaForecast:
    """단기예보 어댑터. Activity `WeatherSource.forecast` 와 같은 모양이다.

    프로세스 하나에 하나를 두고 같이 쓴다 — 캐시가 인스턴스에 있다. `app/api` 가 만들어 주입한다.
    """

    def __init__(
        self,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._client = httpx.AsyncClient(transport=transport)
        self._now = now or (lambda: datetime.now(KST))
        self._cache: OrderedDict[tuple[int, int, str, str], Slots] = OrderedDict()
        self._locks: dict[tuple[int, int, str, str], asyncio.Lock] = {}

    async def aclose(self) -> None:
        await self._client.aclose()

    async def forecast(self, *, grid: WeatherGrid, day: date) -> RawForecast:
        """그날 한 시각 칸의 SKY · PCP · POP 원문.

        칸이 없거나 조회가 실패하면 UpstreamUnavailable — Activity 는 "날씨 확인 못 함"으로 본다.
        """
        now = self._now()
        slots = await self._slots(grid, *latest_base(now))
        slot = slot_for(day, now, slots)
        if slot is None:
            raise UpstreamUnavailable("단기예보에 그날 칸이 없다")
        values = slots[slot]
        return RawForecast(sky=values.get("SKY"), pcp=values.get("PCP"), pop=values.get("POP"))

    async def _slots(self, grid: WeatherGrid, base_date: str, base_time: str) -> Slots:
        key = (grid.nx, grid.ny, base_date, base_time)
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        # 이번 주말(토 · 일)처럼 같은 발표분을 동시에 부르면 한 번만 받는다
        lock = self._locks.setdefault(key, asyncio.Lock())
        try:
            async with lock:
                if key in self._cache:
                    return self._cache[key]
                payload = await get_json(
                    self._client,
                    URL,
                    {
                        "pageNo": 1,
                        "numOfRows": ROWS_PER_CALL,
                        "dataType": "JSON",
                        "base_date": base_date,
                        "base_time": base_time,
                        "nx": grid.nx,
                        "ny": grid.ny,
                    },
                    what="단기예보",
                )
                slots = parse_slots(payload)
                self._cache[key] = slots
                if len(self._cache) > CACHE_SIZE:
                    self._cache.popitem(last=False)
                return slots
        finally:
            # 실패해도 Lock 을 남기지 않는다 (#328 리뷰). 기다리던 호출은 잡은 Lock 으로 이어 간다
            self._locks.pop(key, None)
