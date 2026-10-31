"""장소 조회 tool — 넓게 찾고 가까운 순으로 몇 곳만 · 날씨가 나쁘면 실내 종류만.

- 돌려준 장소는 run state 에 남는다. 출력 검증이 place_name 을 이 표와 대조한다.
- 실패는 빈 목록으로 숨기지 않는다.
"""

from datetime import UTC, date, datetime
from uuid import UUID

import pytest

from app.agents.activity.context import ActivityContext, build_gate
from app.agents.activity.result import ErrorCode
from app.agents.activity.schemas.common import DayLabel, PlaceCategory
from app.agents.activity.schemas.outing import SearchNearbyPlacesArgs
from app.agents.activity.store.inmemory import InMemoryPlaces, in_memory_ports
from app.agents.activity.store.ports import CoarseLocation, PlaceRow
from app.agents.activity.tools.outing import (
    INDOOR_CATEGORIES,
    PLACE_MAX_RADIUS_M,
    PLACE_TOP_K,
    days_of,
    outdoor_on,
    search_nearby_places,
)
from app.agents.activity.weather import WeatherBrief

CHILD = UUID(int=1)
NOW = datetime(2026, 10, 2, 10, tzinfo=UTC)
LOCATION = CoarseLocation(lat=35.18, lon=129.08)
# 공공데이터 기준일. 포트는 싣지만 모델에는 주지 않는다
DATA_DATE = date(2026, 6, 30)


def park(name: str, distance_m: int) -> PlaceRow:
    return PlaceRow(
        name=name,
        category=PlaceCategory.PARK,
        distance_m=distance_m,
        source="city_park",
        data_date=DATA_DATE,
    )


def library(name: str, distance_m: int) -> PlaceRow:
    return PlaceRow(
        name=name,
        category=PlaceCategory.LIBRARY,
        distance_m=distance_m,
        source="library",
        data_date=DATA_DATE,
    )


async def context(rows=(), *, outdoor_ok=True, fail=False, places=None) -> ActivityContext:
    ctx = ActivityContext(
        child_id=CHILD,
        run_id="run-1",
        now=NOW,
        timezone=UTC,
        ports=in_memory_ports(
            CHILD, date(2025, 1, 1), places=places or InMemoryPlaces(rows, fail=fail)
        ),
        location=LOCATION,
    )
    ctx.state.gate = await build_gate(ctx, outdoor_ok=outdoor_ok)
    # 날씨 포트가 없는 context 라 판정을 미리 넣어 둔다 — 장소 조회는 그날 판정을 읽는다 (#377)
    for label in DayLabel:
        for day in days_of(label, ctx.today):
            ctx.state.weather[day] = WeatherBrief(outdoor_ok=outdoor_ok)
    return ctx


def args(category: PlaceCategory, day: DayLabel = DayLabel.TODAY) -> SearchNearbyPlacesArgs:
    return SearchNearbyPlacesArgs(category=category, day=day)


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

    async def test_이름과_종류만_싣고_거리는_주지_않는다(self):
        """이름 여러 곳과 거리를 같이 주면 위치를 거꾸로 짐작할 수 있다 (약관 제8조 ③)."""
        result = await search_nearby_places(
            await context([park("○○어린이공원", 800)]), args(PlaceCategory.PARK)
        )
        assert result.data["places"] == [{"name": "○○어린이공원", "category": "park"}]

    async def test_데이터기준일자는_모델에_주지_않는다(self):
        """기준일은 확인 문구("공공데이터 기준 2026년 6월이에요")의 달이다. 모델은 이름 · 종류만."""
        ctx = await context([park("○○어린이공원", 800), library("○○도서관", 900)])
        result = await search_nearby_places(ctx, args(PlaceCategory.PARK))
        for place in result.data["places"]:
            assert set(place) == {"name", "category"}
        assert "2026-06-30" not in str(result.data)
        # run state 에는 남는다 — 화면이 쓸 수 있게
        assert ctx.state.seen_places["○○어린이공원"].data_date == DATA_DATE

    async def test_돌려준_장소만_run_state_에_남는다(self):
        """출력 검증이 place_name 을 이 표와 대조한다. 안 돌려준 곳은 남기지 않는다."""
        rows = [park(f"공원{i}", 100 * (i + 1)) for i in range(PLACE_TOP_K + 2)]
        ctx = await context(rows)
        await search_nearby_places(ctx, args(PlaceCategory.PARK))
        assert set(ctx.state.seen_places) == {f"공원{i}" for i in range(PLACE_TOP_K)}

    async def test_이름이_같으면_가까운_곳이_남는다(self):
        """체인 키즈카페처럼 이름이 같은 곳이 둘 오면 먼 쪽이 덮어쓰지 않는다 (#600 리뷰)."""
        ctx = await context([park("같은공원", 300), park("같은공원", 900)])
        await search_nearby_places(ctx, args(PlaceCategory.PARK))
        assert ctx.state.seen_places["같은공원"].distance_m == 300

    async def test_없으면_빈_목록이다(self):
        result = await search_nearby_places(await context(), args(PlaceCategory.PARK))
        assert result.success is True
        assert result.data["places"] == []


class TestBadWeather:
    @pytest.mark.parametrize("category", sorted(INDOOR_CATEGORIES))
    async def test_바깥_활동이_어려운_날은_실내_종류를_찾는다(self, category):
        rows = [
            PlaceRow(
                name="실내", category=category, distance_m=500, source="library", data_date=None
            ),
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

    async def test_오늘_비가_와도_주말이_맑으면_공원을_찾는다(self):
        """장소도 그날 판정으로 좁힌다 — 오늘 날씨로 주말 공원을 막지 않는다 (#377)."""
        ctx = await context([park("○○어린이공원", 500)], outdoor_ok=False)
        for day in days_of(DayLabel.THIS_WEEKEND, ctx.today):
            ctx.state.weather[day] = WeatherBrief(outdoor_ok=True)
        result = await search_nearby_places(ctx, args(PlaceCategory.PARK, DayLabel.THIS_WEEKEND))
        assert names(result) == ["○○어린이공원"]


class TestCategoryArg:
    async def test_tool_인자는_글자로_와도_포트에는_enum_으로_넘긴다(self):
        """ToolArgs 가 use_enum_values 라 모델 인자를 검증하면 category 가 글자다(#583 리뷰)."""
        seen: list[object] = []

        class SpyPlaces(InMemoryPlaces):
            async def nearby(self, *, location, category, radius_m):
                seen.append(category)
                return await super().nearby(location=location, category=category, radius_m=radius_m)

        ctx = await context(places=SpyPlaces([park("○○어린이공원", 300)]))
        validated = SearchNearbyPlacesArgs.model_validate({"day": "today", "category": "park"})
        assert type(validated.category) is str

        result = await search_nearby_places(ctx, validated)

        assert names(result) == ["○○어린이공원"]
        assert seen == [PlaceCategory.PARK]
        assert type(seen[0]) is PlaceCategory


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


def test_실내_종류는_도서관과_실내_놀이터다():
    # 체험관은 싣는 데이터가 없어 빼 두었다 — 넣으면 모델이 매번 빈 검색을 한다
    assert INDOOR_CATEGORIES == {PlaceCategory.LIBRARY, PlaceCategory.INDOOR_PLAYGROUND}


class TestOutdoorOn:
    """출력 검증과 장소 조회가 같이 쓰는 그날 판정 (#377)."""

    async def weekend(self, saturday: bool, sunday: bool) -> bool:
        ctx = await context()
        sat, sun = days_of(DayLabel.THIS_WEEKEND, ctx.today)
        ctx.state.weather[sat] = WeatherBrief(outdoor_ok=saturday)
        ctx.state.weather[sun] = WeatherBrief(outdoor_ok=sunday)
        return await outdoor_on(ctx, DayLabel.THIS_WEEKEND)

    async def test_주말은_하루라도_되면_연다(self):
        """보호자가 둘 중 하루를 고른다. 되는 날은 모델이 reason 에 쓴다."""
        assert await self.weekend(saturday=False, sunday=True) is True

    async def test_주말_이틀_다_안_되면_닫는다(self):
        assert await self.weekend(saturday=False, sunday=False) is False

    async def test_판정이_없는_날은_그_자리에서_판정한다(self):
        """날씨 포트가 없으면 묻지 않은 날이라 바깥을 연다(`weather.not_asked`). 판정은 남긴다."""
        ctx = await context()
        ctx.state.weather.clear()
        assert await outdoor_on(ctx, DayLabel.TOMORROW) is True
        assert len(ctx.state.weather) == 1
