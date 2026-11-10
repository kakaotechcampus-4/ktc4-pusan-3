"""행사 조회 tool — 그날 열리는 행사를 가까운 순으로 몇 건만 · 바깥이 어려운 날은 찾지 않는다.

- 어댑터는 위치를 받지 않는다. 전국 목록을 받아 거리는 서버가 계산한다.
- 관람 연령은 가까운 것부터 상세를 물어 아이 월령으로 거른다. 상세를 못 읽은 행사는 뺀다.
- 그날 열리는지는 기간이 아니라 일정으로 본다. 결과에 요청한 날 중 열리는 날 · 그날 장소를 싣는다.
- 돌려준 행사는 run state 에 남는다. 출력 검증이 place_name 을 이 표와 대조한다.
"""

import asyncio
from datetime import UTC, date, datetime, timedelta, timezone
from uuid import UUID

import pytest

from app.agents.activity.areas import find_area, find_province
from app.agents.activity.context import ActivityContext, build_gate
from app.agents.activity.events import schedule_days
from app.agents.activity.result import ErrorCode
from app.agents.activity.schemas.common import DayLabel, PlaceCategory
from app.agents.activity.schemas.outing import SearchEventsArgs, SearchNearbyPlacesArgs
from app.agents.activity.store.inmemory import (
    InMemoryEvents,
    InMemoryEventSchedules,
    in_memory_ports,
)
from app.agents.activity.store.ports import EVENT_TOP_K, CoarseLocation, EventSchedule
from app.agents.activity.tools.outing import days_of, search_events, search_nearby_places
from app.agents.activity.weather import WeatherBrief
from app.core.event_raw import RawEvent, RawEventIntro

CHILD = UUID(int=1)
# 2026-11-02 월요일. 이번 주말은 11-07(토) · 11-08(일)
NOW = datetime(2026, 11, 2, 10, tzinfo=UTC)
LOCATION = CoarseLocation(lat=35.18, lon=129.08)
# 위도 1도 ≈ 111.2km
KM_PER_DEGREE = 111.2


def raw(
    cid: str,
    title: str,
    *,
    km: float = 1.0,
    start="20261101",
    end="20261109",
    near=LOCATION,
    region="26",
):
    return RawEvent(
        content_id=cid,
        title=title,
        start=start,
        end=end,
        mapx=f"{near.lon}",
        mapy=f"{near.lat + km / KM_PER_DEGREE:.6f}",
        kind_code="EV01",
        progress="",
        region_code=region,
    )


async def context(
    events: InMemoryEvents, *, outdoor_ok=True, birth=date(2023, 6, 1), schedules=None
):
    ctx = ActivityContext(
        child_id=CHILD,
        run_id="run-1",
        now=NOW,
        timezone=UTC,
        ports=in_memory_ports(CHILD, birth, events=events, event_schedules=schedules),
        location=LOCATION,
    )
    ctx.state.gate = await build_gate(ctx, outdoor_ok=outdoor_ok)
    # 날씨 포트가 없는 context 라 판정을 미리 넣어 둔다 — 행사 조회는 그날 판정을 읽는다
    for label in DayLabel:
        for day in days_of(label, ctx.today):
            ctx.state.weather[day] = WeatherBrief(outdoor_ok=outdoor_ok)
    return ctx


def names(result) -> list[str]:
    return [event["name"] for event in result.data["events"]]


class SpyEvents(InMemoryEvents):
    """어느 날 기준으로 목록을 물었는지 남긴다."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.cutoffs: list[date] = []

    async def events(self, *, ending_on_or_after):
        self.cutoffs.append(ending_on_or_after)
        return await super().events(ending_on_or_after=ending_on_or_after)


class TestNearest:
    async def test_가까운_순으로_몇_건만_준다(self):
        rows = [raw(str(i), f"축제{i}", km=10 - i) for i in range(8)]
        ctx = await context(InMemoryEvents(rows))

        result = await search_events(ctx, SearchEventsArgs(day=DayLabel.TODAY))

        assert result.success is True
        assert names(result) == ["축제7", "축제6", "축제5", "축제4", "축제3"]
        assert len(names(result)) == EVENT_TOP_K

    async def test_최대_반경_밖은_찾지_않는다(self):
        rows = [raw("1", "가까운 축제", km=3), raw("2", "먼 축제", km=25)]

        result = await search_events(
            await context(InMemoryEvents(rows)), SearchEventsArgs(day=DayLabel.TODAY)
        )

        assert names(result) == ["가까운 축제"]

    async def test_이름_종류_기간_장소_열리는_날만_주고_거리는_주지_않는다(self):
        rows = [raw("1", "바다 축제")]
        intros = {"1": RawEventIntro(age_limit="전 연령", place="광안리해수욕장 일원")}

        result = await search_events(
            await context(InMemoryEvents(rows, intros)), SearchEventsArgs(day=DayLabel.TODAY)
        )

        assert result.data["events"] == [
            {
                "name": "바다 축제",
                "kind": "축제",
                "period": "2026-11-01 ~ 2026-11-09",
                "place": "광안리해수욕장 일원",
                "open_days": [{"date": "2026-11-02", "weekday": "월"}],
            }
        ]

    async def test_어댑터에는_위치를_주지_않고_오늘_이후에_끝나는_목록을_묻는다(self):
        spy = SpyEvents([raw("1", "바다 축제")])

        await search_events(await context(spy), SearchEventsArgs(day=DayLabel.TOMORROW))

        assert spy.cutoffs == [date(2026, 11, 2)]

    async def test_없으면_빈_목록이다(self):
        result = await search_events(
            await context(InMemoryEvents()), SearchEventsArgs(day=DayLabel.TODAY)
        )

        assert result.success is True
        assert result.data["events"] == []


class TestDay:
    async def test_그날_열리는_행사만_찾는다(self):
        rows = [
            raw("1", "오늘 끝나는 축제", start="20261025", end="20261102"),
            raw("2", "내일 여는 축제", start="20261103", end="20261110"),
        ]
        ctx = await context(InMemoryEvents(rows))

        today = await search_events(ctx, SearchEventsArgs(day=DayLabel.TODAY))
        tomorrow = await search_events(ctx, SearchEventsArgs(day=DayLabel.TOMORROW))

        assert names(today) == ["오늘 끝나는 축제"]
        assert names(tomorrow) == ["내일 여는 축제"]

    async def test_바깥_활동이_어려운_날은_찾지_않는다(self):
        spy = SpyEvents([raw("1", "바다 축제")])

        result = await search_events(
            await context(spy, outdoor_ok=False), SearchEventsArgs(day=DayLabel.TODAY)
        )

        assert result.success is False
        assert result.error["code"] == ErrorCode.INVALID_ARGS
        assert spy.cutoffs == []

    async def test_주말은_바깥이_되는_날에_열리는_행사만_찾는다(self):
        rows = [
            raw("1", "토요일 축제", start="20261107", end="20261107"),
            raw("2", "일요일 축제", start="20261108", end="20261108"),
        ]
        ctx = await context(InMemoryEvents(rows))
        # 토요일 비, 일요일 맑음
        ctx.state.weather[date(2026, 11, 7)] = WeatherBrief(outdoor_ok=False)

        result = await search_events(ctx, SearchEventsArgs(day=DayLabel.THIS_WEEKEND))

        assert names(result) == ["일요일 축제"]


SATURDAY = date(2026, 11, 7)


class TestSchedule:
    """기간은 전체 기간이다 — "매주 토요일" 행사는 기간 안 월요일에 없다 (11-03 실측)."""

    async def test_기간_안이어도_그날_열리지_않는_행사는_주지_않는다(self):
        rows = [raw("1", "토요일 체조", km=1), raw("2", "매일 축제", km=2)]
        schedules = InMemoryEventSchedules({"1": EventSchedule(open_days=frozenset({SATURDAY}))})
        ctx = await context(InMemoryEvents(rows), schedules=schedules)

        today = await search_events(ctx, SearchEventsArgs(day=DayLabel.TODAY))
        weekend = await search_events(ctx, SearchEventsArgs(day=DayLabel.THIS_WEEKEND))

        assert names(today) == ["매일 축제"]
        assert names(weekend) == ["토요일 체조", "매일 축제"]

    async def test_요청한_날_중_열리는_날만_싣는다(self):
        schedules = InMemoryEventSchedules({"1": EventSchedule(open_days=frozenset({SATURDAY}))})
        ctx = await context(InMemoryEvents([raw("1", "토요일 체조")]), schedules=schedules)

        result = await search_events(ctx, SearchEventsArgs(day=DayLabel.THIS_WEEKEND))

        (event,) = result.data["events"]
        assert event["open_days"] == [{"date": "2026-11-07", "weekday": "토"}]

    async def test_장소가_날마다_다르면_그날_장소를_준다(self):
        """다른 날 장소를 주면 모델이 그 장소로 문장을 쓴다."""
        intros = {"1": RawEventIntro(age_limit="", place="바람숲공원 / 모래섬 해변")}
        schedules = InMemoryEventSchedules(
            {
                "1": EventSchedule(
                    open_days=frozenset({SATURDAY}),
                    day_places=((date(2026, 10, 31), "바람숲공원"), (SATURDAY, "모래섬 해변")),
                )
            }
        )
        ctx = await context(
            InMemoryEvents([raw("1", "달빛 체조", start="20261001")], intros), schedules=schedules
        )

        result = await search_events(ctx, SearchEventsArgs(day=DayLabel.THIS_WEEKEND))

        (event,) = result.data["events"]
        assert event["place"] == "모래섬 해변"
        assert event["open_days"] == [
            {"date": "2026-11-07", "weekday": "토", "place": "모래섬 해변"}
        ]

    async def test_관람_연령으로_빠진_행사는_일정을_읽지_않는다(self):
        """일정 읽기는 모델 호출이다 — 어차피 빠질 행사에 부르지 않는다."""
        rows = [raw("1", "청소년 축제", km=1), raw("2", "가족 축제", km=2)]
        intros = {"1": RawEventIntro(age_limit="만 13세 이상", place="")}
        schedules = InMemoryEventSchedules()

        await search_events(
            await context(InMemoryEvents(rows, intros), schedules=schedules),
            SearchEventsArgs(day=DayLabel.TODAY),
        )

        assert schedules.calls == ["2"]

    @pytest.mark.parametrize("offset", range(7))
    def test_일정을_읽는_창이_모든_날_라벨을_덮는다(self, offset):
        today = date(2026, 11, 2) + timedelta(days=offset)
        for label in DayLabel:
            assert set(days_of(label, today)) <= set(schedule_days(today))


class TestAge:
    async def test_아이보다_높은_관람_연령은_뺀다(self):
        rows = [raw("1", "청소년 축제", km=1), raw("2", "가족 축제", km=2)]
        intros = {"1": RawEventIntro(age_limit="만 13세 이상", place="")}

        result = await search_events(
            await context(InMemoryEvents(rows, intros)), SearchEventsArgs(day=DayLabel.TODAY)
        )

        assert names(result) == ["가족 축제"]

    async def test_상세를_못_읽은_행사는_뺀다(self):
        rows = [raw("1", "모르는 축제", km=1), raw("2", "가족 축제", km=2)]

        result = await search_events(
            await context(InMemoryEvents(rows, fail_intro={"1"})),
            SearchEventsArgs(day=DayLabel.TODAY),
        )

        assert names(result) == ["가족 축제"]

    async def test_상세는_가까운_것부터_정해진_수까지만_묻는다(self):
        rows = [raw(str(i), f"축제{i}", km=1 + i) for i in range(30)]
        events = InMemoryEvents(rows)

        await search_events(await context(events), SearchEventsArgs(day=DayLabel.TODAY))

        assert events.intro_calls == [str(i) for i in range(EVENT_TOP_K * 2)]


class TestFailure:
    async def test_조회_실패는_빈_목록으로_숨기지_않는다(self):
        result = await search_events(
            await context(InMemoryEvents(fail=True)), SearchEventsArgs(day=DayLabel.TODAY)
        )

        assert result.success is False
        assert result.error["code"] == ErrorCode.UPSTREAM_ERROR


class TestSeen:
    async def test_돌려준_행사만_run_state_에_남는다(self):
        rows = [raw(str(i), f"축제{i}", km=1 + i) for i in range(7)]
        ctx = await context(InMemoryEvents(rows))

        await search_events(ctx, SearchEventsArgs(day=DayLabel.TODAY))

        assert set(ctx.state.seen_events) == {f"축제{i}" for i in range(EVENT_TOP_K)}
        row = ctx.state.seen_events["축제0"]
        assert (row.starts_on, row.ends_on) == (date(2026, 11, 1), date(2026, 11, 9))
        # 출력 검증이 후보의 날로 다시 본다 — 요청한 날만이 아니라 창 전체의 열리는 날이다
        assert row.schedule.open_days == set(schedule_days(date(2026, 11, 2)))


# 보호자가 지역을 말한 요청
REQUEST = ("주말에 해운대 축제 가볼까",)
HAEUNDAE = find_area("해운대")
KST = timezone(timedelta(hours=9))
DAYTIME = datetime(2026, 11, 2, 10, tzinfo=KST)
# 해운대에서 20km 밖인 보호자 위치(서울)
HOME = CoarseLocation(lat=37.57, lon=126.98)


async def area_context(events: InMemoryEvents, *, location=None):
    ctx = ActivityContext(
        child_id=CHILD,
        run_id="run-1",
        now=DAYTIME,
        timezone=KST,
        ports=in_memory_ports(CHILD, date(2023, 6, 1), events=events),
        location=location,
    )
    ctx.state.gate = await build_gate(ctx, outdoor_ok=True)
    ctx.state.request_texts = REQUEST
    return ctx


class TestArea:
    async def test_보호자가_말한_지역에서_찾고_근처_금지_목록에_넣는다(self):
        rows = [
            raw("1", "해운대 모래축제", near=HAEUNDAE.location),
            raw("2", "우리 동네 축제", near=HOME),
        ]
        ctx = await area_context(InMemoryEvents(rows), location=HOME)

        result = await search_events(ctx, SearchEventsArgs(day=DayLabel.TODAY, area="해운대"))

        assert names(result) == ["해운대 모래축제"]
        assert ctx.state.area == HAEUNDAE
        assert ctx.state.area_places == {"해운대 모래축제"}

    async def test_위치도_지역도_없으면_지역을_말해야_찾는다(self):
        ctx = await area_context(InMemoryEvents([raw("1", "바다 축제")]))

        result = await search_events(ctx, SearchEventsArgs(day=DayLabel.TODAY))

        assert result.success is False
        assert result.error["code"] == ErrorCode.INVALID_ARGS

    async def test_지역으로_바꾸면_보호자_위치에서_찾은_행사는_버린다(self):
        rows = [
            raw("1", "해운대 모래축제", near=HAEUNDAE.location),
            raw("2", "우리 동네 축제", near=HOME),
        ]
        ctx = await area_context(InMemoryEvents(rows), location=HOME)

        await search_events(ctx, SearchEventsArgs(day=DayLabel.TODAY))
        assert set(ctx.state.seen_events) == {"우리 동네 축제"}
        await search_events(ctx, SearchEventsArgs(day=DayLabel.TODAY, area="해운대"))

        assert set(ctx.state.seen_events) == {"해운대 모래축제"}


BUSAN = find_province("부산")


class TestSaidArea:
    """지역 이름은 보호자가 쓴 그대로여야 한다 — 해석한 요청에만 있는 지역은 받지 않는다."""

    async def test_해석_문장에만_있는_지역은_받지_않는다(self):
        ctx = await area_context(InMemoryEvents([raw("1", "부산 축제", near=BUSAN.location)]))
        # 앞 추천 이름에서 AI 가 가져온 지역이다 — 보호자는 "다른 데" 라고만 했다
        ctx.state.request_texts = ("부산 말고 다른 곳 놀러 갈 만한 행사 추천",)
        ctx.state.original_texts = ("다른 데는 없어?",)

        result = await search_events(ctx, SearchEventsArgs(day=DayLabel.TODAY, area="부산"))

        assert result.success is False
        assert result.error["code"] == ErrorCode.INVALID_ARGS

    async def test_앞_턴에서_보호자가_말한_지역은_받는다(self):
        """ "해운대 근처 나들이 추천해줘" 뒤 "그럼 다른 데는?" — 지역은 보호자가 앞에서 말했다."""
        rows = [raw("1", "해운대 모래축제", near=HAEUNDAE.location)]
        ctx = await area_context(InMemoryEvents(rows))
        ctx.state.request_texts = ("해운대 근처 다른 나들이 추천",)
        ctx.state.original_texts = ("그럼 다른 데는?",)
        ctx.state.prior_texts = ("해운대 근처 나들이 추천해줘",)

        result = await search_events(ctx, SearchEventsArgs(day=DayLabel.TODAY, area="해운대"))

        assert names(result) == ["해운대 모래축제"]


class TestProvince:
    """시 · 도만 말해도 행사는 그 시 · 도 전체에서 찾는다 — "부산 놀러 갈 건데 행사 추천해줘"."""

    async def province_context(self, events):
        ctx = await area_context(events)
        ctx.state.request_texts = ("부산 놀러 갈 건데 행사 추천해줘",)
        return ctx

    async def test_그_시도의_행사를_대표_지점에서_가까운_순으로_준다(self):
        center = BUSAN.location
        rows = [
            raw("1", "부산 먼 축제", km=25, near=center),  # 20km 밖이어도 부산이다
            raw("2", "경남 가까운 축제", km=5, near=center, region="48"),  # 가까워도 경남이다
            raw("3", "부산 가까운 축제", km=3, near=center),
        ]
        ctx = await self.province_context(InMemoryEvents(rows))

        result = await search_events(ctx, SearchEventsArgs(day=DayLabel.TODAY, area="부산"))

        assert names(result) == ["부산 가까운 축제", "부산 먼 축제"]
        assert ctx.state.area == BUSAN
        assert ctx.state.area_places == {"부산 가까운 축제", "부산 먼 축제"}

    async def test_장소는_시도_전체에서_찾지_않고_안내를_남긴다(self):
        """장소는 넓어서 찾지 않는다 — 행사로 시도를 정한 뒤에도 같다."""
        ctx = await self.province_context(
            InMemoryEvents([raw("3", "부산 축제", near=BUSAN.location)])
        )
        await search_events(ctx, SearchEventsArgs(day=DayLabel.TODAY, area="부산"))

        result = await search_nearby_places(
            ctx, SearchNearbyPlacesArgs(day=DayLabel.TODAY, category=PlaceCategory.PARK)
        )

        assert result.error["code"] == ErrorCode.NOT_FOUND
        assert ctx.state.area_missed is True

    @pytest.mark.parametrize(
        ("area", "region", "province"),
        [("제주", "50", "제주특별자치도"), ("세종", "36110", "세종특별자치시")],
    )
    async def test_시도_이름이_그_시도의_시와_같아도_시도_전체에서_찾는다(
        self, area, region, province
    ):
        """제주 · 세종은 장소 조회에서는 제주시 · 그곳이지만 행사는 시 · 도 코드로 찾는다 (#668)."""
        center = find_province(area).location
        ctx = await self.province_context(
            InMemoryEvents([raw("1", "먼 축제", km=30, near=center, region=region)])
        )
        ctx.state.request_texts = (f"{area} 놀러 갈 건데 행사 추천해줘",)

        result = await search_events(ctx, SearchEventsArgs(day=DayLabel.TODAY, area=area))

        assert names(result) == ["먼 축제"]  # 20km 밖이어도 그 시 · 도다
        assert ctx.state.area.whole_province and ctx.state.area.province == province

    async def test_다른_시도의_시와_겹치는_이름은_모호해서_찾지_않는다(self):
        """광주시는 경기 광주시와 겹친다 — 전남광주 전체 행사를 내지 않는다 (#668 리뷰)."""
        ctx = await self.province_context(
            InMemoryEvents([raw("1", "광주 축제", near=BUSAN.location, region="12")])
        )
        ctx.state.request_texts = ("광주시 놀러 갈 건데 행사 추천해줘",)

        result = await search_events(ctx, SearchEventsArgs(day=DayLabel.TODAY, area="광주시"))

        assert result.error["code"] == ErrorCode.NOT_FOUND
        assert ctx.state.area is None and ctx.state.area_missed is True

    async def test_옛_광역시_이름은_시도_전체가_아니라_그_시에서_찾는다(self):
        """광주광역시는 통합 뒤 시도 이름이 아니다 — 전남 행사까지 넓히지 않는다 (#676)."""
        city = find_area("광주광역시")
        ctx = await self.province_context(
            InMemoryEvents(
                [
                    raw("1", "광주 축제", km=3, near=city.location, region="12"),
                    raw("2", "먼 전남 축제", km=60, near=city.location, region="12"),
                ]
            )
        )
        ctx.state.request_texts = ("광주광역시 놀러 갈 건데 행사 추천해줘",)

        result = await search_events(ctx, SearchEventsArgs(day=DayLabel.TODAY, area="광주광역시"))

        assert names(result) == ["광주 축제"]
        assert ctx.state.area == city and not ctx.state.area.whole_province

    async def test_명소_이름을_통째로_넘기면_지역_이름만_다시_쓰라고_돌려보낸다(self):
        ctx = await self.province_context(InMemoryEvents([raw("1", "바다 축제")]))
        ctx.state.request_texts = ("해운대해수욕장 가는데 행사 추천해줘",)

        result = await search_events(
            ctx, SearchEventsArgs(day=DayLabel.TODAY, area="해운대해수욕장")
        )

        assert result.error["code"] == ErrorCode.INVALID_ARGS
        assert result.resource == "search_events"
        assert "지역 이름만" in result.error["message"]


class TestEndedTonight:
    """오늘 밤에 물으면 운영 시간이 이미 끝난 행사는 오늘에서 뺀다 — 모르면 남긴다 (#800)."""

    async def at(self, hour: int, intros) -> ActivityContext:
        events = InMemoryEvents([raw("1", "빛 축제")], intros=intros)
        ctx = await context(events)
        return ActivityContext(
            child_id=ctx.child_id,
            run_id=ctx.run_id,
            now=datetime(2026, 11, 2, hour, tzinfo=UTC),
            timezone=UTC,
            ports=ctx.ports,
            location=ctx.location,
            state=ctx.state,
        )

    async def test_끝난_행사는_오늘_빼고_내일은_남긴다(self):
        ctx = await self.at(
            21, {"1": RawEventIntro(age_limit="전 연령", place="광장", playtime="10:00~18:00")}
        )
        today = await search_events(ctx, SearchEventsArgs(day="오늘"))
        assert names(today) == []
        tomorrow = await search_events(ctx, SearchEventsArgs(day="내일"))
        assert names(tomorrow) == ["빛 축제"]

    async def test_밤까지_하는_행사는_남긴다(self):
        ctx = await self.at(
            21, {"1": RawEventIntro(age_limit="전 연령", place="광장", playtime="18:00~22:00")}
        )
        assert names(await search_events(ctx, SearchEventsArgs(day="오늘"))) == ["빛 축제"]

    async def test_끝_시각을_모르면_남긴다(self):
        ctx = await self.at(
            21, {"1": RawEventIntro(age_limit="전 연령", place="광장", playtime="(토) 19:00")}
        )
        assert names(await search_events(ctx, SearchEventsArgs(day="오늘"))) == ["빛 축제"]


class TestShownBefore:
    """ "다른 행사 추천해줘" — 앞 턴에서 보여 준 행사는 빼고 그다음 가까운 행사를 준다 (#852)."""

    async def test_앞에서_보여_준_행사는_빼고_다음_행사를_준다(self):
        rows = [raw(str(i), f"축제{i}", km=10 - i) for i in range(8)]
        ctx = await context(InMemoryEvents(rows))
        ctx.state.shown_before = ("축제7 구경하기", "축제6에서 사진 찍기")

        result = await search_events(ctx, SearchEventsArgs(day=DayLabel.TODAY))

        assert names(result) == ["축제5", "축제4", "축제3", "축제2", "축제1"]
        assert ctx.state.no_new_events is False

    async def test_새_행사가_없으면_빈_목록과_안내다(self):
        ctx = await context(InMemoryEvents([raw("1", "축제1")]))
        ctx.state.shown_before = ("축제1 구경하기",)

        result = await search_events(ctx, SearchEventsArgs(day=DayLabel.TODAY))

        assert result.success is True and names(result) == []
        assert "앞에서 보여 준" in result.data["message"]
        assert ctx.state.no_new_events is True

    async def test_보여_준_것을_빼고_남은_것도_연령으로_빠지면_안내다(self):
        rows = [raw("1", "가족 축제", km=1), raw("2", "청소년 축제", km=2)]
        intros = {"2": RawEventIntro(age_limit="만 13세 이상", place="")}
        ctx = await context(InMemoryEvents(rows, intros))
        ctx.state.shown_before = ("가족 축제 구경하기",)

        result = await search_events(ctx, SearchEventsArgs(day=DayLabel.TODAY))

        assert names(result) == [] and ctx.state.no_new_events is True


class TestOrder:
    """읽는 순서 — 1년 내내 하는 곳은 다음에도 갈 수 있어 뒤로 미루되 빼지는 않는다.
    보호자 위치에서 찾으면 가까운 묶음(10km)이 먼저다 (#856 리뷰)."""

    async def test_가까운_묶음_안에서는_짧은_행사가_먼저다(self):
        rows = [
            raw("1", "상설 전시", km=1, start="20260101", end="20261231"),
            raw("2", "두 달 축제", km=2, start="20261001", end="20261210"),
            raw("3", "주말 축제", km=8, start="20261101", end="20261109"),
        ]
        ctx = await context(InMemoryEvents(rows))

        result = await search_events(ctx, SearchEventsArgs(day=DayLabel.TODAY))

        assert names(result) == ["주말 축제", "두 달 축제", "상설 전시"]

    async def test_멀리_있는_짧은_행사는_가까운_상설_행사_뒤다(self):
        rows = [
            raw("1", "먼 주말 축제", km=18, start="20261101", end="20261109"),
            raw("2", "상설 전시", km=1, start="20260101", end="20261231"),
        ]
        ctx = await context(InMemoryEvents(rows))

        result = await search_events(ctx, SearchEventsArgs(day=DayLabel.TODAY))

        assert names(result) == ["상설 전시", "먼 주말 축제"]

    async def test_말한_지역에서는_거리보다_짧은_행사가_먼저다(self):
        """지역을 말했으면 거리는 그 지역 대표 지점에서 잰 값이라 보호자에게 뜻이 없다."""
        rows = [
            raw(
                "1",
                "해운대 상설 전시",
                km=1,
                near=HAEUNDAE.location,
                start="20260101",
                end="20261231",
            ),
            raw("2", "해운대 주말 축제", km=15, near=HAEUNDAE.location),
        ]
        ctx = await area_context(InMemoryEvents(rows), location=HOME)

        result = await search_events(ctx, SearchEventsArgs(day=DayLabel.TODAY, area="해운대"))

        assert names(result) == ["해운대 주말 축제", "해운대 상설 전시"]


class SlowSchedules(InMemoryEventSchedules):
    """일정 읽기(모델 호출)가 느린 행사. `slow` 의 행사는 `delay` 초 걸린다."""

    def __init__(self, slow: set[str], delay: float) -> None:
        super().__init__()
        self.slow, self.delay, self.done = slow, delay, []

    async def schedule_of(self, event, *, today):
        if event.content_id in self.slow:
            await asyncio.sleep(self.delay)
        self.done.append(event.content_id)
        return await super().schedule_of(event, today=today)


class TestReadBudget:
    """첫 run 이 20초를 넘지 않게 일정 읽기에 상한을 둔다. 못 끝낸 행사는 이번에는 빼고, 읽기는
    멈추지 않고 이어서 캐시를 채운다 — 다음 run 부터는 나온다."""

    async def test_상한_안에_못_읽은_행사는_이번에는_빼고_읽기는_이어간다(self, monkeypatch):
        from app.agents.activity.tools import outing

        monkeypatch.setattr(outing, "EVENT_READ_BUDGET_S", 0.05)
        rows = [raw("1", "빠른 축제", km=1), raw("2", "느린 축제", km=2)]
        schedules = SlowSchedules({"2"}, delay=0.2)
        ctx = await context(InMemoryEvents(rows), schedules=schedules)

        result = await search_events(ctx, SearchEventsArgs(day=DayLabel.TODAY))

        assert names(result) == ["빠른 축제"]
        await asyncio.sleep(0.3)
        assert "2" in schedules.done  # 뒤에서 끝까지 읽었다

    async def test_마감이_가까우면_남은_시간만큼만_기다린다(self, monkeypatch):
        import time

        from app.agents.activity.tools import outing

        monkeypatch.setattr(outing, "EVENT_READ_BUDGET_S", 5.0)
        rows = [raw("1", "느린 축제", km=1)]
        ctx = await context(InMemoryEvents(rows), schedules=SlowSchedules({"1"}, delay=1.0))
        # 마감까지 남은 시간이 마지막 후보 내기 몫보다 적다 — 일정 읽기를 기다리지 않는다
        ctx.state.deadline = time.perf_counter() + outing.FINAL_TURN_RESERVE_S

        started = time.perf_counter()
        result = await search_events(ctx, SearchEventsArgs(day=DayLabel.TODAY))

        assert names(result) == [] and time.perf_counter() - started < 0.5

    async def test_보여_준_행사_뒤_남은_것이_모두_상한에_걸리면_새_행사_없음이_아니다(
        self, monkeypatch
    ):
        """앞 턴에서 보여 준 행사를 빼고 남은 행사가 전부 일정 읽기 중이면 "더 없다" 가 아니라
        "확인 중" 이다 (#906 리뷰)."""
        from app.agents.activity.tools import outing

        monkeypatch.setattr(outing, "EVENT_READ_BUDGET_S", 0.01)
        rows = [raw("1", "본 축제", km=1), raw("2", "느린 축제", km=2)]
        ctx = await context(InMemoryEvents(rows), schedules=SlowSchedules({"2"}, delay=0.2))
        ctx.state.shown_before = ("본 축제",)

        result = await search_events(ctx, SearchEventsArgs(day=DayLabel.TODAY))

        assert names(result) == []
        assert ctx.state.no_new_events is False
        assert ctx.state.events_pending is True
        assert "확인하는 중" in result.data["message"]


class CachingSlowSchedules(SlowSchedules):
    """한 번 읽은 행사는 캐시에서 바로 준다 — `ReadEventSchedules` 의 일정 캐시처럼."""

    async def schedule_of(self, event, *, today):
        if event.content_id in self.done:
            return await InMemoryEventSchedules.schedule_of(self, event, today=today)
        return await super().schedule_of(event, today=today)


class TestPrefetch:
    """나들이를 물은 run 은 날씨를 보는 동안 근처 행사 일정 읽기를 뒤에서 시작한다 — 모델이
    행사를 찾을 때는 캐시에 있다."""

    async def test_미리_읽어_두면_상한이_짧아도_행사가_나온다(self, monkeypatch):
        from app.agents.activity.tools import outing

        monkeypatch.setattr(outing, "EVENT_READ_BUDGET_S", 0.05)
        schedules = CachingSlowSchedules({"1"}, delay=0.1)
        ctx = await context(InMemoryEvents([raw("1", "느린 축제", km=1)]), schedules=schedules)
        ctx.state.request_texts = ("주말에 갈만한 행사 추천해줘",)

        outing.start_event_prefetch(ctx, _weather(ok=True))
        await asyncio.sleep(0.3)  # 날씨 조회 · 모델 1회차 동안
        result = await search_events(ctx, SearchEventsArgs(day=DayLabel.TODAY))

        assert names(result) == ["느린 축제"]

    async def test_나들이를_묻지_않았으면_미리_읽지_않는다(self):
        from app.agents.activity.tools import outing

        schedules = CachingSlowSchedules(set(), delay=0)
        ctx = await context(InMemoryEvents([raw("1", "축제")]), schedules=schedules)
        ctx.state.request_texts = ("오늘 집에서 뭐 하고 놀까",)

        outing.start_event_prefetch(ctx, _weather(ok=True))
        await asyncio.sleep(0.05)

        assert schedules.calls == []


def _weather(*, ok: bool):
    future = asyncio.get_event_loop().create_future()
    future.set_result(WeatherBrief(outdoor_ok=ok))
    return future


class TestPrefetchLimits:
    """미리 읽기가 쓸모없는 run 에서는 모델을 부르지 않는다 (#906 리뷰)."""

    async def test_오늘_바깥이_어려우면_일정은_읽지_않는다(self):
        from app.agents.activity.tools import outing

        schedules = CachingSlowSchedules(set(), delay=0)
        ctx = await context(InMemoryEvents([raw("1", "축제")]), schedules=schedules)
        ctx.state.request_texts = ("주말에 갈만한 행사 추천해줘",)

        outing.start_event_prefetch(ctx, _weather(ok=False))
        await asyncio.sleep(0.05)

        assert schedules.calls == []

    async def test_지역을_말했으면_위치_근처를_미리_읽지_않는다(self):
        from app.agents.activity.tools import outing

        schedules = CachingSlowSchedules(set(), delay=0)
        ctx = await context(InMemoryEvents([raw("1", "축제")]), schedules=schedules)
        ctx.state.request_texts = ("다음 주에 의령 가는데 갈만한 행사 있어?",)

        outing.start_event_prefetch(ctx, _weather(ok=True))
        await asyncio.sleep(0.05)

        assert schedules.calls == []

    async def test_앞_턴이_아니라_이번_말만_본다(self):
        from app.agents.activity.tools import outing

        schedules = CachingSlowSchedules(set(), delay=0)
        ctx = await context(InMemoryEvents([raw("1", "축제")]), schedules=schedules)
        ctx.state.request_texts = ("오늘 집에서 뭐 하고 놀까",)
        ctx.state.prior_texts = ("주말에 갈만한 행사 추천해줘",)

        outing.start_event_prefetch(ctx, _weather(ok=True))
        await asyncio.sleep(0.05)

        assert schedules.calls == []

    async def test_가까운_순으로_다섯_건만_읽는다(self):
        from app.agents.activity.tools import outing

        schedules = CachingSlowSchedules(set(), delay=0)
        rows = [raw(str(n), f"축제 {n}", km=n) for n in range(1, 9)]
        ctx = await context(InMemoryEvents(rows), schedules=schedules)
        ctx.state.request_texts = ("주말에 갈만한 행사 추천해줘",)

        outing.start_event_prefetch(ctx, _weather(ok=True))
        await asyncio.sleep(0.05)

        assert sorted(schedules.done) == ["1", "2", "3", "4", "5"]
