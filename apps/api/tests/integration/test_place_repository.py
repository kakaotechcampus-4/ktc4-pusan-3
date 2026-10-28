"""장소 표 · 저장소 · 어댑터를 실제 DB 로 (#516).

- 표가 막는 값(source · category · 시군구 코드 · 한국 상자)
- 다시 실어도 행이 늘지 않는다 · 이번 받기에 없는 행만 지운다
- 반경 검색 — 가까운 순 · 반경 밖 제외 · TOP_K · 종류 · 거리 절대값 · tool 경로

장소 이름 · 좌표는 지어낸 값이다. 기준점에서 방위 · 거리로 계산한 점에 둔다.
"""

import math
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from uuid import UUID

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from app.agents.activity.context import ActivityContext, build_gate
from app.agents.activity.schemas.common import DayLabel, PlaceCategory
from app.agents.activity.schemas.outing import SearchNearbyPlacesArgs
from app.agents.activity.store.inmemory import in_memory_ports
from app.agents.activity.store.ports import PLACE_MAX_RADIUS_M, PLACE_TOP_K, CoarseLocation
from app.agents.activity.tools.outing import days_of, search_nearby_places
from app.agents.activity.weather import WeatherBrief
from app.domains.place import repository
from app.domains.place.agent_reader import DbPlaceSource
from app.domains.place.models import Place
from app.domains.place.repository import EARTH_RADIUS_M

NOW = datetime(2026, 10, 27, tzinfo=UTC)
CENTER = CoarseLocation(lat=35.18, lon=129.08)


def _destination(distance_m: float, bearing_deg: float = 0.0) -> tuple[float, float]:
    """기준점에서 방위 · 거리만큼 간 점 (구면 공식)."""
    d = distance_m / EARTH_RADIUS_M
    b = math.radians(bearing_deg)
    p1, l1 = math.radians(CENTER.lat), math.radians(CENTER.lon)
    p2 = math.asin(math.sin(p1) * math.cos(d) + math.cos(p1) * math.sin(d) * math.cos(b))
    l2 = l1 + math.atan2(
        math.sin(b) * math.sin(d) * math.cos(p1), math.cos(d) - math.sin(p1) * math.sin(p2)
    )
    return math.degrees(p2), math.degrees(l2)


def values(
    source_id: str,
    *,
    source: str = "city_park",
    category: str = "park",
    name: str | None = None,
    distance_m: float = 1_000,
    bearing: float = 0.0,
    **over,
) -> dict:
    lat, lng = _destination(distance_m, bearing)
    return {
        "source": source,
        "source_id": source_id,
        "name": name or f"시험장소{source_id}",
        "category": category,
        "lat": lat,
        "lng": lng,
        "sigungu_code": None,
        "data_date": date(2026, 6, 30),
        "has_playground": False,
        "area_m2": None,
        "source_version": "test",
        "synced_at": NOW,
    } | over


@pytest.fixture
def opener(session):
    @asynccontextmanager
    async def same():
        yield session

    return same


@pytest.fixture(autouse=True)
async def _empty_place(session):
    """개발 DB 에 적재본이 있어도 빈 표에서 시작한다(#583 리뷰 — "절반 미만 거부" 에 걸렸다).

    TRUNCATE 도 테스트의 바깥 트랜잭션 안이라 conftest 의 마지막 롤백이 되돌린다 — 적재본은
    지워지지 않는다. 바로 commit 해서 savepoint 를 닫는다 — 테스트 안의 rollback(적재 거절)이
    TRUNCATE 까지 되감지 않게.
    """
    await session.execute(text("TRUNCATE place"))
    await session.commit()


async def _count(session, source: str | None = None) -> int:
    stmt = select(func.count()).select_from(Place)
    if source is not None:
        stmt = stmt.where(Place.source == source)
    return await session.scalar(stmt)


# ── 표가 막는 값 ──────────────────────────────────────────────────────
async def test_기본값으로_넣을_수_있다(session):
    session.add(Place(**values("11110-00001", sigungu_code="11110")))
    await session.flush()


@pytest.mark.parametrize(
    "bad",
    [
        {"source": "kids_cafe"},
        {"category": "cafe"},
        {"sigungu_code": "1111"},
        {"sigungu_code": "ABCDE"},
        {"lat": 129.08, "lng": 35.18},  # 위경도가 뒤바뀌었다
        {"lat": 0.0, "lng": 0.0},
    ],
)
async def test_place_가_막는_값(session, bad):
    session.add(Place(**values("11110-00001") | bad))
    with pytest.raises(IntegrityError):
        await session.flush()


async def test_같은_소스의_같은_번호는_두_번_넣지_못한다(session):
    session.add_all([Place(**values("1")), Place(**values("1", name="다른 이름"))])
    with pytest.raises(IntegrityError):
        await session.flush()


async def test_소스가_다르면_번호가_같아도_된다(session):
    session.add_all(
        [Place(**values("1")), Place(**values("1", source="library", category="library"))]
    )
    await session.flush()


# ── 덮기 · 지우기 ─────────────────────────────────────────────────────
async def test_다시_실어도_행이_늘지_않고_값은_새로_덮는다(session):
    await repository.upsert_places(session, rows=[values("1"), values("2")])
    later = datetime(2026, 11, 1, tzinfo=UTC)
    await repository.upsert_places(
        session,
        rows=[values("1", name="새 이름", synced_at=later), values("2", synced_at=later)],
    )

    assert await _count(session) == 2
    row = await session.scalar(select(Place).where(Place.source_id == "1"))
    await session.refresh(row)
    assert (row.name, row.synced_at) == ("새 이름", later)


async def test_한_번에_같은_번호를_두_번_싣지_않는다(session):
    with pytest.raises(ValueError):
        await repository.upsert_places(session, rows=[values("1"), values("1")])


async def test_이번_받기에_없는_행만_지운다(session):
    await repository.upsert_places(
        session,
        rows=[
            values("1"),
            values("2"),
            values("3"),
            values("2", source="library", category="library"),  # 다른 소스는 그대로
        ],
    )

    deleted = await repository.delete_missing(session, source="city_park", keep_ids={"1", "3"})

    assert deleted == 1
    assert await repository.stored_ids(session, source="city_park") == {"1", "3"}
    assert await repository.stored_ids(session, source="library") == {"2"}


async def test_남길_번호가_비면_지우지_않는다(session):
    await repository.upsert_places(session, rows=[values("1")])

    with pytest.raises(ValueError):
        await repository.delete_missing(session, source="city_park", keep_ids=set())

    assert await _count(session) == 1


# ── 반경 검색 ─────────────────────────────────────────────────────────
async def _seed(session, rows):
    await repository.upsert_places(session, rows=rows)


async def test_가까운_순으로_반경_안만_준다(session, opener):
    await _seed(
        session,
        [
            values("far", distance_m=PLACE_MAX_RADIUS_M + 300, bearing=90),
            values("mid", distance_m=5_000, bearing=200),
            values("near", distance_m=300, bearing=45),
            values("edge", distance_m=PLACE_MAX_RADIUS_M - 300, bearing=300),
        ],
    )

    rows = await DbPlaceSource(opener).nearby(
        location=CENTER, category=PlaceCategory.PARK, radius_m=PLACE_MAX_RADIUS_M
    )

    assert [row.name for row in rows] == ["시험장소near", "시험장소mid", "시험장소edge"]
    assert [row.source for row in rows] == ["city_park"] * 3
    assert [row.data_date for row in rows] == [date(2026, 6, 30)] * 3
    # 거리는 서버가 계산한 정수 미터다. 같은 반지름의 구면 공식이라 몇 미터 안에서 맞는다
    for row, expected in zip(rows, [300, 5_000, PLACE_MAX_RADIUS_M - 300], strict=True):
        assert isinstance(row.distance_m, int)
        assert abs(row.distance_m - expected) <= 2


async def test_반경을_좁히면_그_밖은_빠진다(session, opener):
    await _seed(session, [values("a", distance_m=800), values("b", distance_m=1_500, bearing=180)])

    rows = await DbPlaceSource(opener).nearby(
        location=CENTER, category=PlaceCategory.PARK, radius_m=1_000
    )

    assert [row.name for row in rows] == ["시험장소a"]


async def test_가까운_순_TOP_K_곳만_준다(session, opener):
    await _seed(
        session,
        [values(f"p{k}", distance_m=500 * (k + 1), bearing=37 * k) for k in range(PLACE_TOP_K + 3)],
    )

    rows = await DbPlaceSource(opener).nearby(
        location=CENTER, category=PlaceCategory.PARK, radius_m=PLACE_MAX_RADIUS_M
    )

    assert [row.name for row in rows] == [f"시험장소p{k}" for k in range(PLACE_TOP_K)]


async def test_고른_종류만_준다(session, opener):
    await _seed(
        session,
        [
            values("park", distance_m=200),
            values("lib", source="library", category="library", distance_m=100),
        ],
    )

    parks = await DbPlaceSource(opener).nearby(
        location=CENTER, category=PlaceCategory.PARK, radius_m=PLACE_MAX_RADIUS_M
    )
    libraries = await DbPlaceSource(opener).nearby(
        location=CENTER, category=PlaceCategory.LIBRARY, radius_m=PLACE_MAX_RADIUS_M
    )
    playgrounds = await DbPlaceSource(opener).nearby(
        location=CENTER, category=PlaceCategory.INDOOR_PLAYGROUND, radius_m=PLACE_MAX_RADIUS_M
    )

    assert [(row.name, row.category) for row in parks] == [("시험장소park", PlaceCategory.PARK)]
    assert [(row.name, row.source) for row in libraries] == [("시험장소lib", "library")]
    assert playgrounds == []  # 근처에 없는 것은 실패가 아니다


async def test_놀이터로_찾으면_놀이터가_있는_공원도_온다(session, opener):
    await _seed(
        session,
        [
            values("kid", distance_m=300, has_playground=True),
            values("plain", distance_m=200),  # 놀이터 없는 공원
            values("lib", source="library", category="library", distance_m=100),
            values(
                "pg",
                source="kids_play_facility",
                category="playground",
                distance_m=500,
                has_playground=True,
            ),
        ],
    )

    playgrounds = await DbPlaceSource(opener).nearby(
        location=CENTER, category=PlaceCategory.PLAYGROUND, radius_m=PLACE_MAX_RADIUS_M
    )
    parks = await DbPlaceSource(opener).nearby(
        location=CENTER, category=PlaceCategory.PARK, radius_m=PLACE_MAX_RADIUS_M
    )

    # 놀이터가 있는 공원은 종류가 park 그대로 온다. 놀이터 자체(playground)도 온다
    assert [(row.name, row.category) for row in playgrounds] == [
        ("시험장소kid", PlaceCategory.PARK),
        ("시험장소pg", PlaceCategory.PLAYGROUND),
    ]
    # 공원으로 찾을 때는 놀이터 여부와 상관없다
    assert [row.name for row in parks] == ["시험장소plain", "시험장소kid"]


async def test_거리_절대값이_맞다_서울시청에서_부산시청까지(session):
    # 같은 식으로 잰 값이 325,099m 다(#583 리뷰). 실제 거리(약 325km)와 1km 안에서 맞는다.
    # 어댑터는 반경을 20km 로 자르므로 저장소를 바로 부른다
    await repository.upsert_places(session, rows=[values("busan", lat=35.1798, lng=129.0750)])

    [row] = await repository.nearby(
        session, lat=37.5663, lng=126.9779, category="park", radius_m=400_000, limit=5
    )

    assert abs(row.distance_m - 325_099) <= 1_000


async def test_tool_경로로_불러도_DB_에서_찾는다(session, opener):
    """모델 인자 검증 → tool → DB 어댑터. 인자의 종류가 글자로 온다(use_enum_values, #583 리뷰)."""
    await _seed(session, [values("near", distance_m=400), values("far", distance_m=900)])
    child = UUID(int=1)
    ctx = ActivityContext(
        child_id=child,
        run_id="run-1",
        now=NOW,
        timezone=UTC,
        ports=in_memory_ports(child, date(2025, 1, 1), places=DbPlaceSource(opener)),
        location=CENTER,
    )
    ctx.state.gate = await build_gate(ctx, outdoor_ok=True)
    for day in days_of(DayLabel.TODAY, ctx.today):
        ctx.state.weather[day] = WeatherBrief(outdoor_ok=True)
    args = SearchNearbyPlacesArgs.model_validate({"day": "today", "category": "park"})

    result = await search_nearby_places(ctx, args)

    assert result.success is True
    assert [place["name"] for place in result.data["places"]] == ["시험장소near", "시험장소far"]


async def test_면적은_비워도_된다(session):
    await repository.upsert_places(
        session, rows=[values("1", area_m2=1500.5), values("2", area_m2=None)]
    )

    areas = dict((await session.execute(select(Place.source_id, Place.area_m2))).all())
    assert areas == {"1": 1500.5, "2": None}


async def test_같은_거리면_이름_순이다(session, opener):
    # 같은 점에 둔 두 곳 — run 을 다시 돌려도 같은 곳이 같은 순서로 나와야 한다
    await _seed(
        session,
        [values("1", name="나공원", distance_m=700), values("2", name="가공원", distance_m=700)],
    )

    rows = await DbPlaceSource(opener).nearby(
        location=CENTER, category=PlaceCategory.PARK, radius_m=PLACE_MAX_RADIUS_M
    )

    assert [row.name for row in rows] == ["가공원", "나공원"]


async def test_상자_가장자리의_장소도_빠지지_않는다(session, opener):
    # 상자가 원보다 작으면 반경 안의 동 · 서 · 남 · 북 끝 장소가 SQL 에서 먼저 빠진다
    await _seed(
        session,
        [
            values(f"b{bearing}", distance_m=PLACE_MAX_RADIUS_M - 50, bearing=bearing)
            for bearing in (0, 90, 180, 270)
        ],
    )

    rows = await DbPlaceSource(opener).nearby(
        location=CENTER, category=PlaceCategory.PARK, radius_m=PLACE_MAX_RADIUS_M
    )

    assert sorted(row.name for row in rows) == sorted(f"시험장소b{b}" for b in (0, 90, 180, 270))
