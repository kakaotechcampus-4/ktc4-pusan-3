"""장소 조회 tool — 넓게 찾고 가까운 순으로 몇 곳만 · 날씨가 나쁘면 실내 종류만.

- 돌려준 장소는 run state 에 남는다. 출력 검증이 place_name 을 이 표와 대조한다.
- 실패는 빈 목록으로 숨기지 않는다.
"""

from datetime import UTC, date, datetime
from uuid import UUID

import pytest

from app.agents.activity.context import ActivityContext, build_gate
from app.agents.activity.result import ErrorCode
from app.agents.activity.schemas.common import PlaceCategory
from app.agents.activity.schemas.outing import SearchNearbyPlacesArgs
from app.agents.activity.store.inmemory import InMemoryPlaces, in_memory_ports
from app.agents.activity.store.ports import CoarseLocation, PlaceRow
from app.agents.activity.tools.outing import (
    INDOOR_CATEGORIES,
    PLACE_MAX_RADIUS_M,
    PLACE_TOP_K,
    search_nearby_places,
)

CHILD = UUID(int=1)
NOW = datetime(2026, 10, 2, 10, tzinfo=UTC)
LOCATION = CoarseLocation.of(35.18, 129.08)


def park(name: str, distance_m: int) -> PlaceRow:
    return PlaceRow(
        name=name, category=PlaceCategory.PARK, distance_m=distance_m, source="city_park"
    )


def library(name: str, distance_m: int) -> PlaceRow:
    return PlaceRow(
        name=name, category=PlaceCategory.LIBRARY, distance_m=distance_m, source="library"
    )


async def context(rows=(), *, outdoor_ok=True, fail=False) -> ActivityContext:
    ctx = ActivityContext(
        child_id=CHILD,
        run_id="run-1",
        now=NOW,
        timezone=UTC,
        ports=in_memory_ports(CHILD, date(2025, 1, 1), places=InMemoryPlaces(rows, fail=fail)),
        location=LOCATION,
    )
    ctx.state.gate = await build_gate(ctx, outdoor_ok=outdoor_ok)
    return ctx


def args(category: PlaceCategory) -> SearchNearbyPlacesArgs:
    return SearchNearbyPlacesArgs(category=category)


def names(result) -> list[str]:
    return [place["name"] for place in result.data["places"]]


class TestNearest:
    async def test_가까운_순으로_몇_곳만_준다(self):
        rows = [park(f"공원{i}", 1_000 * (10 - i)) for i in range(10)]
        result = await search_nearby_places(await context(rows), args(PlaceCategory.PARK))
        assert result.success is True
        assert names(result) == ["공원9", "공원8", "공원7", "공원6", "공원5"]
        assert len(names(result)) == PLACE_TOP_K

    async def test_5km_밖이라도_최대_반경_안이면_찾는다(self):
        """5km 로 자르면 군 지역에서 0곳이 돼 외출 요청에 장소가 빠진다 (#193 리뷰)."""
        rows = [park("먼 공원", 12_000), park("너무 먼 공원", PLACE_MAX_RADIUS_M + 1)]
        result = await search_nearby_places(await context(rows), args(PlaceCategory.PARK))
        assert names(result) == ["먼 공원"]

    async def test_이름_종류_거리만_싣는다(self):
        result = await search_nearby_places(
            await context([park("○○어린이공원", 800)]), args(PlaceCategory.PARK)
        )
        assert result.data["places"] == [
            {"name": "○○어린이공원", "category": "park", "distance_m": 800}
        ]

    async def test_돌려준_장소만_run_state_에_남는다(self):
        """출력 검증이 place_name 을 이 표와 대조한다. 안 돌려준 곳은 남기지 않는다."""
        rows = [park(f"공원{i}", 100 * (i + 1)) for i in range(PLACE_TOP_K + 2)]
        ctx = await context(rows)
        await search_nearby_places(ctx, args(PlaceCategory.PARK))
        assert set(ctx.state.seen_places) == {f"공원{i}" for i in range(PLACE_TOP_K)}

    async def test_없으면_빈_목록이다(self):
        result = await search_nearby_places(await context(), args(PlaceCategory.PARK))
        assert result.success is True
        assert result.data["places"] == []


class TestBadWeather:
    @pytest.mark.parametrize("category", sorted(INDOOR_CATEGORIES))
    async def test_바깥_활동이_어려운_날은_실내_종류를_찾는다(self, category):
        rows = [
            PlaceRow(name="실내", category=category, distance_m=500, source="library"),
        ]
        result = await search_nearby_places(await context(rows, outdoor_ok=False), args(category))
        assert names(result) == ["실내"]

    @pytest.mark.parametrize("category", [PlaceCategory.PARK, PlaceCategory.PLAYGROUND])
    async def test_바깥_종류는_고쳐_다시_부르게_한다(self, category):
        ctx = await context([park("○○어린이공원", 500)], outdoor_ok=False)
        result = await search_nearby_places(ctx, args(category))
        assert result.success is False
        assert result.error["code"] == ErrorCode.INVALID_ARGS
        assert "library" in result.error["message"]
        assert ctx.state.seen_places == {}


class TestFailure:
    async def test_조회_실패는_빈_목록으로_숨기지_않는다(self):
        result = await search_nearby_places(await context(fail=True), args(PlaceCategory.PARK))
        assert result.success is False
        assert result.error["code"] == ErrorCode.UPSTREAM_ERROR

    async def test_위치_없이_불리면_버그다(self):
        """registry 가 위치 없으면 열지 않는다. 불렸다면 그쪽이 틀렸다."""
        ctx = await context()
        no_location = ActivityContext(
            child_id=ctx.child_id,
            run_id=ctx.run_id,
            now=ctx.now,
            timezone=ctx.timezone,
            ports=ctx.ports,
            state=ctx.state,
        )
        with pytest.raises(RuntimeError):
            await search_nearby_places(no_location, args(PlaceCategory.PARK))


def test_실내_종류는_정해진_셋이다():
    assert INDOOR_CATEGORIES == {
        PlaceCategory.LIBRARY,
        PlaceCategory.INDOOR_PLAYGROUND,
        PlaceCategory.EXPERIENCE_CENTER,
    }
