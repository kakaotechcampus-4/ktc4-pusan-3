"""장소 저장소. 적재 스크립트(`scripts/load_places.py`)가 쓰고 어댑터(`agent_reader.py`)가 읽는다.
값은 칸 이름 → 값 dict 로 받는다.

- 적재는 (source, source_id) 로 새 값을 덮는다(`upsert_places`). 이번 받기에 없는 행은 지운다
  (`delete_missing`) — 문 닫은 곳이 "근처에 있어요" 로 계속 나가지 않게. 장소 행은 근거가 아니라
  다른 표가 가리키지 않아서 지워도 끊기는 것이 없다.
- 반경 검색(`nearby`)은 `nearby_places.sql` 이다 — EXPLAIN 을 봐야 하는 쿼리라 SQL 로 쓴다
  (apps/api CLAUDE.md). 🚨 기준 좌표는 인자로만 받고 어디에도 남기지 않는다.
"""

import math
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy import Text, all_, bindparam, delete, select, text
from sqlalchemy.dialects.postgresql import ARRAY, insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.place.models import Place

# 한 문장에 싣는 행 수. 칸이 12개라 PostgreSQL 인자 상한(32,767)에 걸리지 않는 크기다
CHUNK = 1000
# 평균 지구 반지름. 상자(`bounding_box`)와 거리(`nearby_places.sql`)가 같은 값을 쓴다
EARTH_RADIUS_M = 6_371_008.8

# 다시 받을 때 고치는 칸. 키(source · source_id)만 빼고 전부다
_UPDATED = (
    "name",
    "category",
    "lat",
    "lng",
    "sigungu_code",
    "data_date",
    "has_playground",
    "area_m2",
    "source_version",
    "synced_at",
)

_NEARBY_SQL = text(
    Path(__file__).with_name("nearby_places.sql").read_text(encoding="utf-8")
).bindparams(bindparam("sources", type_=ARRAY(Text)))


@dataclass(frozen=True)
class NearbyPlace:
    name: str
    category: str
    source: str
    data_date: date | None
    distance_m: float


async def stored_ids(session: AsyncSession, *, source: str) -> set[str]:
    """그 소스로 지금 실려 있는 source_id. 적재가 지울 행과 거절 기준(절반)을 센다."""
    stmt = select(Place.source_id).where(Place.source == source)
    return set((await session.scalars(stmt)).all())


async def upsert_places(session: AsyncSession, *, rows: Sequence[Mapping[str, Any]]) -> None:
    keys = [(row["source"], row["source_id"]) for row in rows]
    if len(set(keys)) != len(keys):
        # 한 문장 안에서 같은 키를 두 번 덮으면 PostgreSQL 이 문장째 거절한다. 적재가 먼저 하나로
        # 모은다
        raise ValueError("같은 (source, source_id) 가 두 번 들어왔다")
    for start in range(0, len(rows), CHUNK):
        stmt = insert(Place).values(list(rows[start : start + CHUNK]))
        stmt = stmt.on_conflict_do_update(
            index_elements=["source", "source_id"],
            set_={column: stmt.excluded[column] for column in _UPDATED},
        )
        await session.execute(stmt)


async def delete_missing(session: AsyncSession, *, source: str, keep_ids: Collection[str]) -> int:
    """그 소스에서 이번 받기에 없는 행을 지운다. 지운 수를 돌려준다.

    번호 목록은 배열 인자 하나로 넘긴다(`<> ALL`) — 공원만 1만 행이 넘어서 `NOT IN` 으로 펼치면
    인자 수가 상한에 가까워진다.
    """
    if not keep_ids:
        # 빈 받기로 부르면 그 소스가 통째로 지워진다. 빈 받기는 호출부가 먼저 막는다
        raise ValueError("남길 장소가 비어 있다")
    keep = bindparam("keep_ids", sorted(keep_ids), type_=ARRAY(Text))
    stmt = delete(Place).where(Place.source == source, Place.source_id != all_(keep))
    return (await session.execute(stmt)).rowcount


def bounding_box(lat: float, lng: float, radius_m: float) -> tuple[float, float, float, float]:
    """반경 원을 다 덮는 위경도 상자 (lat_min, lat_max, lng_min, lng_max).

    경도 1도의 길이는 위도가 높을수록 짧아서, 상자 안에서 적도에서 가장 먼 위도로 경도 폭을 잡는다
    — 그래야 원의 위 · 아래 끝에서도 상자가 모자라지 않는다.
    """
    dlat = math.degrees(radius_m / EARTH_RADIUS_M)
    far_lat = min(abs(lat) + dlat, 89.0)
    dlng = math.degrees(radius_m / (EARTH_RADIUS_M * math.cos(math.radians(far_lat))))
    return lat - dlat, lat + dlat, lng - dlng, lng + dlng


async def nearby(
    session: AsyncSession,
    *,
    lat: float,
    lng: float,
    category: str,
    radius_m: float,
    limit: int,
    sources: Collection[str],
) -> list[NearbyPlace]:
    """`radius_m` 안의 `category` 장소를 가까운 순 `limit` 곳. `sources` 출처의 행만 본다."""
    lat_min, lat_max, lng_min, lng_max = bounding_box(lat, lng, radius_m)
    result = await session.execute(
        _NEARBY_SQL,
        {
            "lat": float(lat),
            "lng": float(lng),
            "category": category,
            "lat_min": lat_min,
            "lat_max": lat_max,
            "lng_min": lng_min,
            "lng_max": lng_max,
            "radius_m": float(radius_m),
            "earth_radius_m": EARTH_RADIUS_M,
            "limit": limit,
            "sources": sorted(sources),
        },
    )
    return [
        NearbyPlace(
            name=row.name,
            category=row.category,
            source=row.source,
            data_date=row.data_date,
            distance_m=row.distance_m,
        )
        for row in result
    ]
