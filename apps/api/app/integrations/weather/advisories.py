"""기상청 기상특보 특보코드조회(`WthrWrnInfoService/getPwnCd`) — 이 위치의 특보구역 행.

하는 일 (설계 D8 · 4-2)
  1. 전국 특보를 지난 `LOOKBACK_DAYS` 일치 한 번에 받는다. 위치를 보내지 않는다 — 특보 쪽으로는
     위치 파생값이 하나도 나가지 않는다. 받은 목록은 모든 사용자가 같이 쓰므로 `CACHE_MINUTES`
     동안 프로세스에 하나만 둔다
  2. 격자의 시군구에 걸리는 특보구역(`areas.zones_of`)의 행만 남겨 원문 그대로 넘긴다

기간 없이 부르면 지난 발표분이 빠지고, 발표 이력(`getWthrWrnList`)은 해제된 특보도 남아서 쓰지
않는다. 발효 여부를 (구역, 종류)마다 가장 최근 행으로 읽는 것은 Activity `weather.py` 다.
결과 코드는 그대로 넘긴다 — `03 NO_DATA` 는 실패가 아니라 "특보 없음"이다.
"""

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from app.core.weather_raw import RawAdvisories, RawAdvisoryRow, UpstreamUnavailable, WeatherGrid
from app.integrations.weather.areas import nearest_district, zones_of
from app.integrations.weather.public_data import get_json, result_code

log = logging.getLogger(__name__)

URL = "https://apis.data.go.kr/1360000/WthrWrnInfoService/getPwnCd"
KST = ZoneInfo("Asia/Seoul")

# 며칠 전에 발표돼 아직 발효 중인 특보(긴 폭염)를 놓치지 않게 넉넉히. 30일 전국이 250행 남짓,
# 0.6초였다(2026-10 실호출). 최대 조회 기간은 60일이다 (잠정)
LOOKBACK_DAYS = 30
# 한 번에 다 받는다. 쪽을 나누면 호출이 늘어 시간 예산을 넘긴다
ROWS = 5000
# 전국 목록을 같이 쓰는 시간 (잠정). 특보는 발표 즉시 반영돼야 해서 짧게 둔다
CACHE_MINUTES = 10

_OK = "00"
_NO_DATA = "03"


def parse_rows(payload: dict[str, Any]) -> tuple[str, tuple[RawAdvisoryRow, ...]]:
    """(결과 코드, 전국 행). 숫자로 온 칸도 문자열로 바꾼다. 모양이 이상한 행은 실패다."""
    code = result_code(payload)
    if code is None:
        raise UpstreamUnavailable("특보 응답에 결과 코드가 없다")
    if code != _OK:
        return code, ()
    items = ((payload["response"].get("body") or {}).get("items") or {}).get("item") or []
    if isinstance(items, dict):
        items = [items]
    try:
        rows = tuple(
            RawAdvisoryRow(
                area_code=str(item["areaCode"]),
                warn_var=str(item["warnVar"]),
                warn_stress=str(item["warnStress"]),
                command=str(item["command"]),
                tm_fc=str(item["tmFc"]),
                tm_seq=str(item["tmSeq"]),
            )
            for item in items
        )
    except (KeyError, TypeError):
        raise UpstreamUnavailable("특보 행 모양이 다르다") from None
    return code, rows


class KmaAdvisories:
    """특보 어댑터. Activity `WeatherSource.advisories` 와 같은 모양이다. 프로세스에 하나를 둔다."""

    def __init__(
        self,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._client = httpx.AsyncClient(transport=transport)
        self._now = now or (lambda: datetime.now(KST))
        self._cached: tuple[datetime, str, tuple[RawAdvisoryRow, ...]] | None = None
        self._lock = asyncio.Lock()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def advisories(self, *, grid: WeatherGrid) -> RawAdvisories:
        """이 격자의 특보구역 행. 구역을 못 정하거나 조회가 실패하면 UpstreamUnavailable."""
        district = nearest_district(grid)
        zones = zones_of(district) if district is not None else frozenset()
        if not zones:
            raise UpstreamUnavailable("격자에 맞는 특보구역이 없다")
        code, rows = await self._nationwide()
        if code != _OK:
            return RawAdvisories(result_code=code)
        return RawAdvisories(
            result_code=code, rows=tuple(row for row in rows if row.area_code in zones)
        )

    async def _nationwide(self) -> tuple[str, tuple[RawAdvisoryRow, ...]]:
        now = self._now().astimezone(KST)
        async with self._lock:
            if self._cached is not None and now - self._cached[0] < timedelta(
                minutes=CACHE_MINUTES
            ):
                return self._cached[1], self._cached[2]
            payload = await get_json(
                self._client,
                URL,
                {
                    "pageNo": 1,
                    "numOfRows": ROWS,
                    "dataType": "JSON",
                    "fromTmFc": (now - timedelta(days=LOOKBACK_DAYS)).strftime("%Y%m%d"),
                    "toTmFc": now.strftime("%Y%m%d"),
                },
                what="특보",
            )
            code, rows = parse_rows(payload)
            if code in (_OK, _NO_DATA):
                # 실패 코드는 캐시하지 않는다 — 다음 run 이 다시 묻는다
                self._cached = (now, code, rows)
            return code, rows
