"""장소 어댑터 — DB 없이 볼 수 있는 것 (#516).

- 상자(`bounding_box`)가 반경 원을 다 덮는다 — 모자라면 가까운 장소가 SQL 에서 먼저 빠진다
- 반경 · 곳 수 상한을 포트 계약 값으로 건다
- 🚨 실패하면 UpstreamUnavailable 이고 문구 · 로그 · 원인 예외에 보호자 좌표가 없다
- 표의 닫힌 값(source · category CHECK)이 Activity 포트의 값과 같고, 마이그레이션의 글자와도 같다

실제 SQL 은 tests/integration/test_place_repository.py 가 본다.
"""

import importlib.util
import logging
import math
import typing
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy.exc import OperationalError

from app.agents.activity.context import ActivityContext, build_gate
from app.agents.activity.schemas.common import DayLabel, PlaceCategory
from app.agents.activity.schemas.outing import SearchNearbyPlacesArgs
from app.agents.activity.store.inmemory import in_memory_ports
from app.agents.activity.store.ports import (
    PLACE_MAX_RADIUS_M,
    PLACE_SOURCES,
    PLACE_TOP_K,
    CoarseLocation,
    PlaceRow,
    UpstreamUnavailable,
)
from app.agents.activity.tools.outing import days_of, search_nearby_places
from app.agents.activity.weather import WeatherBrief
from app.domains.place import repository
from app.domains.place.agent_reader import DbPlaceSource
from app.domains.place.models import CATEGORY_CHECK, SOURCE_CHECK
from app.domains.place.repository import EARTH_RADIUS_M, NearbyPlace, bounding_box

# 지어낸 기준점. 흐린 좌표(소수 둘째 자리)라 문구에 "35.18" 이 나오는지로 샌 것을 잡는다
LOCATION = CoarseLocation(lat=35.18, lon=129.08)
COORD_TEXTS = ("35.18", "129.08")


def _destination(lat: float, lng: float, bearing_deg: float, distance_m: float):
    """기준점에서 방위 · 거리만큼 간 점 (구면 공식)."""
    d = distance_m / EARTH_RADIUS_M
    b = math.radians(bearing_deg)
    p1, l1 = math.radians(lat), math.radians(lng)
    p2 = math.asin(math.sin(p1) * math.cos(d) + math.cos(p1) * math.sin(d) * math.cos(b))
    l2 = l1 + math.atan2(
        math.sin(b) * math.sin(d) * math.cos(p1), math.cos(d) - math.sin(p1) * math.sin(p2)
    )
    return math.degrees(p2), math.degrees(l2)


@pytest.mark.parametrize("lat", [33.2, 35.18, 37.56, 38.9])
@pytest.mark.parametrize("radius_m", [500, 5_000, PLACE_MAX_RADIUS_M])
def test_상자는_반경_원을_다_덮는다(lat, radius_m):
    lng = 127.0
    lat_min, lat_max, lng_min, lng_max = bounding_box(lat, lng, radius_m)

    for bearing in range(0, 360, 15):
        p_lat, p_lng = _destination(lat, lng, bearing, radius_m * 0.999)
        assert lat_min <= p_lat <= lat_max, bearing
        assert lng_min <= p_lng <= lng_max, bearing


def test_상자는_원보다_크게_넉넉하지_않다():
    # 너무 크면 상자 거르기가 하는 일이 없다. 20km 원의 상자는 한 변이 40km 남짓이다
    lat_min, lat_max, lng_min, lng_max = bounding_box(35.18, 129.08, PLACE_MAX_RADIUS_M)

    height_m = math.radians(lat_max - lat_min) * EARTH_RADIUS_M
    width_m = math.radians(lng_max - lng_min) * EARTH_RADIUS_M * math.cos(math.radians(35.18))
    diameter = 2 * PLACE_MAX_RADIUS_M
    assert diameter - 1 <= height_m < diameter * 1.01
    assert diameter - 1 <= width_m < diameter * 1.01


# ── 어댑터 ────────────────────────────────────────────────────────────
@pytest.fixture
def opener():
    @asynccontextmanager
    async def fake():
        yield object()

    return fake


def _capture(monkeypatch, rows=()):
    calls: list[dict] = []

    async def nearby(session, **kwargs):
        calls.append(kwargs)
        return list(rows)

    monkeypatch.setattr(repository, "nearby", nearby)
    return calls


async def test_반경은_상한으로_자르고_곳_수는_TOP_K_로_건다(monkeypatch, opener):
    calls = _capture(monkeypatch)

    await DbPlaceSource(opener).nearby(
        location=LOCATION, category=PlaceCategory.PARK, radius_m=PLACE_MAX_RADIUS_M * 5
    )

    [call] = calls
    assert call["radius_m"] == PLACE_MAX_RADIUS_M
    assert call["limit"] == PLACE_TOP_K
    assert call["category"] == "park"
    assert (call["lat"], call["lng"]) == (35.18, 129.08)


async def test_상한보다_좁은_반경은_그대로_쓴다(monkeypatch, opener):
    calls = _capture(monkeypatch)

    await DbPlaceSource(opener).nearby(
        location=LOCATION, category=PlaceCategory.LIBRARY, radius_m=3_000
    )

    assert calls[0]["radius_m"] == 3_000


async def test_거리는_미터_정수로_데이터기준일자는_그대로_옮긴다(monkeypatch, opener):
    _capture(
        monkeypatch,
        [
            NearbyPlace(
                name="시험공원",
                category="park",
                source="city_park",
                data_date=date(2026, 6, 30),
                distance_m=1234.6,
            )
        ],
    )

    rows = await DbPlaceSource(opener).nearby(
        location=LOCATION, category=PlaceCategory.PARK, radius_m=PLACE_MAX_RADIUS_M
    )

    assert rows == [
        PlaceRow(
            name="시험공원",
            category=PlaceCategory.PARK,
            distance_m=1235,
            source="city_park",
            data_date=date(2026, 6, 30),
        )
    ]


@pytest.mark.parametrize("category", ["park", "playground", PlaceCategory.LIBRARY])
async def test_종류는_글자로_와도_받는다(monkeypatch, opener, category):
    # ToolArgs 가 use_enum_values 라 tool 인자는 글자다. 글자에 .value 를 부르면 AttributeError 로
    # Activity 전체가 실패했다(#583 리뷰)
    calls = _capture(monkeypatch)

    await DbPlaceSource(opener).nearby(
        location=LOCATION, category=category, radius_m=PLACE_MAX_RADIUS_M
    )

    assert calls[0]["category"] == PlaceCategory(category).value


async def test_닫힌_값_밖의_종류는_DB_를_열기_전에_거절한다(monkeypatch, opener):
    calls = _capture(monkeypatch)

    with pytest.raises(ValueError):
        await DbPlaceSource(opener).nearby(
            location=LOCATION, category="cafe", radius_m=PLACE_MAX_RADIUS_M
        )
    assert calls == []


async def test_tool_경로로_불러도_DB_어댑터가_찾는다(monkeypatch, opener):
    """모델 인자 검증 → tool → 어댑터. 단위 테스트가 enum 만 넘겨서 놓친 경로다(#583 리뷰)."""
    calls = _capture(
        monkeypatch,
        [
            NearbyPlace(
                name="시험공원",
                category="park",
                source="city_park",
                data_date=None,
                distance_m=420.0,
            )
        ],
    )
    child = UUID(int=1)
    ctx = ActivityContext(
        child_id=child,
        run_id="run-1",
        now=datetime(2026, 10, 2, 10, tzinfo=UTC),
        timezone=UTC,
        ports=in_memory_ports(child, date(2025, 1, 1), places=DbPlaceSource(opener)),
        location=LOCATION,
    )
    ctx.state.gate = await build_gate(ctx, outdoor_ok=True)
    for day in days_of(DayLabel.TODAY, ctx.today):
        ctx.state.weather[day] = WeatherBrief(outdoor_ok=True)
    args = SearchNearbyPlacesArgs.model_validate({"day": "today", "category": "park"})

    result = await search_nearby_places(ctx, args)

    assert result.success is True
    assert result.data["places"] == [{"name": "시험공원", "category": "park"}]
    assert calls[0]["category"] == "park"


async def test_놀이터로_찾은_공원은_종류가_park_그대로다(monkeypatch, opener):
    # 놀이터로 찾으면 놀이터가 있는 공원도 온다(SQL). 종류는 표에 실린 값을 옮긴다
    calls = _capture(
        monkeypatch,
        [
            NearbyPlace(
                name="시험어린이공원",
                category="park",
                source="city_park",
                data_date=None,
                distance_m=300.0,
            )
        ],
    )

    [row] = await DbPlaceSource(opener).nearby(
        location=LOCATION, category=PlaceCategory.PLAYGROUND, radius_m=PLACE_MAX_RADIUS_M
    )

    assert calls[0]["category"] == "playground"
    assert row.category is PlaceCategory.PARK


@pytest.mark.parametrize("radius_m", [0, -1])
async def test_반경이_0_이하면_호출부_버그다(opener, radius_m):
    with pytest.raises(ValueError):
        await DbPlaceSource(opener).nearby(
            location=LOCATION, category=PlaceCategory.PARK, radius_m=radius_m
        )


def _assert_no_coords(error: BaseException) -> None:
    assert error.__cause__ is None and error.__suppress_context__
    for text in COORD_TEXTS:
        assert text not in str(error)


async def test_DB_오류는_UpstreamUnavailable_이고_좌표가_따라가지_않는다(
    monkeypatch, opener, caplog
):
    async def broken(session, **kwargs):
        # DB 오류 글에는 쿼리 인자가 실릴 수 있다
        raise OperationalError("SELECT ...", {"lat": 35.18, "lng": 129.08}, Exception("끊김"))

    monkeypatch.setattr(repository, "nearby", broken)

    with caplog.at_level(logging.WARNING), pytest.raises(UpstreamUnavailable) as caught:
        await DbPlaceSource(opener).nearby(
            location=LOCATION, category=PlaceCategory.PARK, radius_m=PLACE_MAX_RADIUS_M
        )

    _assert_no_coords(caught.value)
    assert "OperationalError" in caplog.text
    for text in COORD_TEXTS:
        assert text not in caplog.text


async def test_연결을_못_열어도_UpstreamUnavailable_이다():
    @asynccontextmanager
    async def refused():
        raise ConnectionRefusedError("127.0.0.1:5432")
        yield

    with pytest.raises(UpstreamUnavailable) as caught:
        await DbPlaceSource(refused).nearby(
            location=LOCATION, category=PlaceCategory.PARK, radius_m=PLACE_MAX_RADIUS_M
        )

    _assert_no_coords(caught.value)


async def test_버그는_삼키지_않는다(monkeypatch, opener):
    async def buggy(session, **kwargs):
        raise KeyError("distance_m")

    monkeypatch.setattr(repository, "nearby", buggy)

    with pytest.raises(KeyError):
        await DbPlaceSource(opener).nearby(
            location=LOCATION, category=PlaceCategory.PARK, radius_m=PLACE_MAX_RADIUS_M
        )


# ── 표의 닫힌 값 = 포트의 값 ──────────────────────────────────────────
def _quoted(check: str) -> set[str]:
    return set(check.split("IN (", 1)[1].rstrip(")").replace("'", "").replace(" ", "").split(","))


def test_category_CHECK_는_PlaceCategory_와_같다():
    assert _quoted(CATEGORY_CHECK) == {category.value for category in PlaceCategory}


def test_source_CHECK_는_PlaceRow_source_와_같다():
    allowed = set(typing.get_args(typing.get_type_hints(PlaceRow)["source"]))

    assert set(PLACE_SOURCES) == allowed
    assert _quoted(SOURCE_CHECK) == allowed


def _migration():
    path = Path(__file__).resolve().parents[3] / "alembic" / "versions"
    [file] = path.glob("5bdc0aad2e70_*.py")
    spec = importlib.util.spec_from_file_location("place_migration", file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_마이그레이션의_source_CHECK_는_PLACE_SOURCES_와_같다():
    # 마이그레이션은 앱 코드를 import 하지 않고 글자를 박아 둔다. 포트에 소스를 더하고
    # 마이그레이션을 안 고치면 적재가 CHECK 에 걸린다
    migration = _migration()

    assert migration.SOURCE == SOURCE_CHECK
    assert migration.CATEGORY == CATEGORY_CHECK
