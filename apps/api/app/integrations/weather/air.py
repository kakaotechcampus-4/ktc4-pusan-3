"""에어코리아 측정소별 실시간(`ArpltnInforInqireSvc/getMsrstnAcctoRltmMesureDnsty`) — 가장 가까운
측정소의 최근 한 시각.

하는 일 (설계 D8)
  1. 격자에서 가장 가까운 측정소를 구운 목록(`stations_data.py`)에서 고른다. 목록 API 와 좌표로
     근접 측정소를 묻는 `getNearbyMsrstnList` 는 런타임에 부르지 않는다 — 밖으로 나가는 위치
     파생값은 측정소 이름뿐이다 (4-3)
  2. 그 측정소의 최근 한 시각 값을 받아 캐시한다 — 키는 (측정소, 지금 시각의 시)다. 에어코리아는
     한 시간에 한 번 갱신되고, 개발계정 쿼터가 500건/일이라 같은 시간에 두 번 부르지 않는다
  3. PM10 · PM2.5 · O3 의 값과 Flag 를 원문 그대로 넘긴다. 측정기가 멈춘 "-" · "통신장애" 를 읽는
     것은 Activity `weather.py` 다
"""

import asyncio
import logging
from collections import OrderedDict
from collections.abc import Callable
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

from app.core.weather_raw import RawAir, UpstreamUnavailable, WeatherGrid
from app.integrations.weather.public_data import get_json, result_code
from app.integrations.weather.stations_data import STATIONS

log = logging.getLogger(__name__)

URL = "https://apis.data.go.kr/B552584/ArpltnInforInqireSvc/getMsrstnAcctoRltmMesureDnsty"
KST = ZoneInfo("Asia/Seoul")

# 이 격자 거리(칸, 1칸 = 5km)보다 먼 측정소는 쓰지 않는다 — 섬처럼 측정소가 없는 곳에 육지 값을
# 쓰지 않는다. 6칸이면 약 30km (잠정)
MAX_STATION_CELLS = 6

CACHE_SIZE = 512
_OK = "00"

Station = tuple[str, int, int]


def nearest_station(grid: WeatherGrid, stations: tuple[Station, ...] = STATIONS) -> str | None:
    """격자에서 가장 가까운 측정소 이름. `MAX_STATION_CELLS` 안에 없으면 None.

    거리는 격자 칸 단위의 직선거리다. 같은 거리면 이름 순으로 앞의 것 — 매번 같은 곳을 고른다.
    """
    best: tuple[int, str] | None = None
    for name, nx, ny in stations:
        distance = (nx - grid.nx) ** 2 + (ny - grid.ny) ** 2
        if best is None or (distance, name) < best:
            best = (distance, name)
    if best is None or best[0] > MAX_STATION_CELLS**2:
        return None
    return best[1]


def parse_air(payload: dict) -> RawAir:
    """응답의 첫 행(최근 시각)을 원문 그대로. 행이 없으면 실패다."""
    code = result_code(payload)
    if code != _OK:
        raise UpstreamUnavailable(f"대기질 결과 코드 {code}")
    items = (payload["response"].get("body") or {}).get("items") or []
    if not items:
        raise UpstreamUnavailable("대기질 응답에 행이 없다")
    row = items[0]

    def raw(key: str) -> str | None:
        value = row.get(key)
        return None if value is None else str(value)

    return RawAir(
        pm10=raw("pm10Value"),
        pm10_flag=raw("pm10Flag"),
        pm25=raw("pm25Value"),
        pm25_flag=raw("pm25Flag"),
        o3=raw("o3Value"),
        o3_flag=raw("o3Flag"),
    )


class AirKorea:
    """대기질 어댑터. Activity `WeatherSource.air_quality` 와 같은 모양이다.

    프로세스 하나에 하나를 두고 같이 쓴다 — 캐시가 인스턴스에 있다. `app/api` 가 만들어 주입한다.
    🚨 api 는 `--workers 1` 전제다(#134). 워커를 늘리면 캐시가 워커마다 따로 생겨 같은 시간에
    같은 측정소를 워커 수만큼 부른다(개발계정 500건/일). 단기예보 · 자외선 · 특보도 같은 구조다.
    """

    def __init__(
        self,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        now: Callable[[], datetime] | None = None,
        stations: tuple[Station, ...] = STATIONS,
    ) -> None:
        self._client = httpx.AsyncClient(transport=transport)
        self._now = now or (lambda: datetime.now(KST))
        self._stations = stations
        self._cache: OrderedDict[tuple[str, str], RawAir] = OrderedDict()
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}

    async def aclose(self) -> None:
        await self._client.aclose()

    async def air_quality(self, *, grid: WeatherGrid) -> RawAir:
        """가장 가까운 측정소의 최근 값. 측정소가 없거나 조회가 실패하면 UpstreamUnavailable."""
        station = nearest_station(grid, self._stations)
        if station is None:
            raise UpstreamUnavailable("가까운 대기질 측정소가 없다")
        key = (station, self._now().astimezone(KST).strftime("%Y%m%d%H"))
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
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
                        "numOfRows": 1,
                        "returnType": "json",
                        "stationName": station,
                        "dataTerm": "DAILY",
                        "ver": "1.0",
                    },
                    what="대기질",
                )
                air = parse_air(payload)
                self._cache[key] = air
                if len(self._cache) > CACHE_SIZE:
                    self._cache.popitem(last=False)
                return air
        finally:
            # 실패해도 Lock 을 남기지 않는다 (#328 리뷰)
            self._locks.pop(key, None)
