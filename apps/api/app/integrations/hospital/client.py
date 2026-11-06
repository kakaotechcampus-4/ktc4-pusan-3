"""국립중앙의료원 병·의원 · 응급의료기관 어댑터 — Health `FacilitySource` 포트의 실제 구현.

- 격자 → 시군구(날씨 변환표) → **시도 이름**으로 묻는다. 이 API 는 시군구를 이름으로만 받아서,
  이름표를 따로 두는 대신 시도 단위로 받고 거리는 Health 쪽이 서버 안에서 계산한다. 구 경계 근처
  보호자에게 옆 구의 가까운 병원도 잡힌다. 밖으로 나가는 위치 값은 시도 이름뿐이다.
- 소아청소년과(`QD=D002`) 목록은 시도마다 하루 동안 기억한다 — 진료시간은 자주 안 바뀐다
  (Health_Tool_명세 "24h 캐시"). 응급의료기관 목록은 시도당 수십 곳이라 10분만 기억한다.
- 해석하지 않는다 — 진료시간 원문을 그대로 싣고 "지금 진료 중" 은 Health 가 판정한다.
- 실패는 `UpstreamUnavailable` 이다. 빈 목록으로 메우지 않는다.
"""

import asyncio
import logging
import math
import time
from collections.abc import Callable
from typing import Any

import httpx

from app.core.hospital_raw import FacilityTier, RawFacility
from app.core.weather_raw import UpstreamUnavailable, WeatherGrid
from app.integrations.hospital.provinces import province_name
from app.integrations.weather.areas import nearest_district
from app.integrations.weather.public_data import get_json, result_code

log = logging.getLogger(__name__)

BASE = "https://apis.data.go.kr/B552657"
CLINIC_URL = f"{BASE}/HsptlAsembySearchService/getHsptlMdcncListInfoInqire"
EMERGENCY_URL = f"{BASE}/ErmctInfoInqireService/getEgytListInfoInqire"

PEDIATRICS = "D002"  # 진료과목 코드 — 소아청소년과
PAGE_SIZE = 1000
CLINIC_TTL_S = 24 * 60 * 60
EMERGENCY_TTL_S = 10 * 60

_CACHE_LIMIT = 32  # 시도는 16개 — 종류 둘을 다 담아도 넘지 않는다


class NmcHospitals:
    def __init__(
        self,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client = httpx.AsyncClient(transport=transport)
        self._clock = clock
        self._cache: dict[tuple[str, str], tuple[float, list[RawFacility]]] = {}
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}

    async def aclose(self) -> None:
        await self._client.aclose()

    async def facilities(self, *, grid: WeatherGrid, tier: FacilityTier) -> list[RawFacility]:
        district = nearest_district(grid)
        province = province_name(district) if district else None
        if province is None:
            return []  # 바다 한가운데 · 표에 없는 시도 — 찾을 지역이 없다
        kind = "emergency" if tier is FacilityTier.EMERGENCY_ROOM else "clinic"
        rows = await self._cached(province, kind)
        # 같은 소아청소년과 목록을 낮에는 소아청소년과로, 밤에는 야간 · 휴일 진료로 싣는다
        return [_with_tier(row, tier) for row in rows]

    async def _cached(self, province: str, kind: str) -> list[RawFacility]:
        key = (province, kind)
        ttl = EMERGENCY_TTL_S if kind == "emergency" else CLINIC_TTL_S
        hit = self._cache.get(key)
        if hit is not None and self._clock() - hit[0] < ttl:
            return hit[1]
        # 같은 시도를 동시에 여러 요청이 받으러 가지 않게
        async with self._locks.setdefault(key, asyncio.Lock()):
            hit = self._cache.get(key)
            if hit is not None and self._clock() - hit[0] < ttl:
                return hit[1]
            rows = await (
                self._emergency(province) if kind == "emergency" else self._clinics(province)
            )
            if len(self._cache) >= _CACHE_LIMIT:
                self._cache.clear()
            self._cache[key] = (self._clock(), rows)
            return rows

    async def _clinics(self, province: str) -> list[RawFacility]:
        items = await self._all_pages(
            CLINIC_URL, {"Q0": province, "QD": PEDIATRICS}, what="병·의원 목록"
        )
        return [row for item in items if (row := _clinic(item)) is not None]

    async def _emergency(self, province: str) -> list[RawFacility]:
        items = await self._all_pages(EMERGENCY_URL, {"Q0": province}, what="응급의료기관 목록")
        return [row for item in items if (row := _emergency(item)) is not None]

    async def _all_pages(
        self, url: str, params: dict[str, Any], *, what: str
    ) -> list[dict[str, Any]]:
        """첫 페이지로 전체 건수를 보고 나머지 페이지는 동시에 받는다."""
        first, total = await self._page(url, params, page=1, what=what)
        pages = math.ceil(total / PAGE_SIZE)
        rest = await asyncio.gather(
            *(self._page(url, params, page=page, what=what) for page in range(2, pages + 1))
        )
        return first + [item for items, _ in rest for item in items]

    async def _page(
        self, url: str, params: dict[str, Any], *, page: int, what: str
    ) -> tuple[list[dict[str, Any]], int]:
        payload = await get_json(
            self._client,
            url,
            {**params, "pageNo": page, "numOfRows": PAGE_SIZE, "_type": "json"},
            what=what,
        )
        code = result_code(payload)
        if code != "00":
            log.warning("병원 조회 결과 코드 what=%s code=%s", what, code)
            raise UpstreamUnavailable(f"{what} 결과 코드 {code}")
        body = (payload.get("response") or {}).get("body") or {}
        items = body.get("items") or {}
        item = items.get("item", []) if isinstance(items, dict) else []
        # 한 건이면 목록이 아니라 객체 하나로 온다
        rows = [item] if isinstance(item, dict) else [i for i in item if isinstance(i, dict)]
        try:
            total = int(body.get("totalCount") or 0)
        except (TypeError, ValueError):
            total = len(rows)
        return rows, total


def _with_tier(row: RawFacility, tier: FacilityTier) -> RawFacility:
    if row.tier is tier:
        return row
    return RawFacility(
        name=row.name, tier=tier, lat=row.lat, lon=row.lon, phone=row.phone, hours=row.hours
    )


def _coords(item: dict[str, Any]) -> tuple[float, float] | None:
    try:
        lat, lon = float(item["wgs84Lat"]), float(item["wgs84Lon"])
    except (KeyError, TypeError, ValueError):
        return None  # 좌표 없는 기관은 거리를 못 재서 싣지 않는다
    return lat, lon


def _text(item: dict[str, Any], key: str) -> str | None:
    value = item.get(key)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _hhmm(value: Any) -> str | None:
    """진료시간 칸. 숫자로 오기도 해서("900") 네 자리로 맞춘다."""
    text = _text({"v": value}, "v")
    if text is None or not text.isdigit():
        return None
    return text.zfill(4)


def _clinic(item: dict[str, Any]) -> RawFacility | None:
    coords, name = _coords(item), _text(item, "dutyName")
    if coords is None or name is None:
        return None
    hours = tuple(
        (_hhmm(item.get(f"dutyTime{slot}s")), _hhmm(item.get(f"dutyTime{slot}c")))
        for slot in range(1, 9)
    )
    return RawFacility(
        name=name,
        tier=FacilityTier.PEDIATRIC_CLINIC,
        lat=coords[0],
        lon=coords[1],
        phone=_text(item, "dutyTel1"),
        hours=hours,
    )


def _emergency(item: dict[str, Any]) -> RawFacility | None:
    coords, name = _coords(item), _text(item, "dutyName")
    if coords is None or name is None:
        return None
    # 응급실 전화(dutyTel3)가 있으면 그쪽이다
    phone = _text(item, "dutyTel3") or _text(item, "dutyTel1")
    return RawFacility(
        name=name, tier=FacilityTier.EMERGENCY_ROOM, lat=coords[0], lon=coords[1], phone=phone
    )
