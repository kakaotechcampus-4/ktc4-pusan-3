"""create_event 의 요일 반복 (#738).

요일마다 되풀이되는 일정은 `create_event` 한 번에 초안 한 장으로 만든다. 날짜마다 따로 부르지
않는다 — 어느 날이 회차인지는 규칙(`rules/recurrence`)이 정하고, 승인할 때 회차 행으로 펼쳐진다.

오늘은 2026-11-05(목)이다. 11/9 는 월요일, 11/7 은 토요일이다.
"""

from datetime import date, datetime
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest

from app.agents.memory.context import AgentContext
from app.agents.memory.drafts import RepeatRule
from app.agents.memory.registry import execute_tool
from app.agents.memory.result import ErrorCode
from app.agents.memory.store import InMemoryStore
from app.core.event_draft import CreateEventDraft, CreateRepeatEventDraft

KST = ZoneInfo("Asia/Seoul")
NOW = datetime(2026, 11, 5, 9, 0, tzinfo=KST)


@pytest.fixture
def context() -> AgentContext:
    return AgentContext(
        child_id=UUID(int=1),
        source_writer=UUID(int=2),
        now=NOW,
        timezone=KST,
        store=InMemoryStore(now=NOW),
    )


async def _create(context: AgentContext, **args: Any):
    return await execute_tool(
        "create_event",
        {
            "title": "등원",
            "starts_on": "11월 7일",
            "starts_time": "오전 8시 30분",
            "repeat_weekdays": ["mon", "wed"],
            "repeat_until": "12월 20일",
            **args,
        },
        context,
    )


async def test_repeat_makes_one_draft_at_the_first_occurrence(context):
    result = await _create(context)

    assert result.success is True, result.error
    [draft] = context.drafts.all()
    # 11/7 은 토요일이라 첫 회차는 다음 월요일이다. 같은 벽시계 시각을 지킨다
    assert draft.starts_at == datetime(2026, 11, 9, 8, 30, tzinfo=KST)
    assert draft.repeat == RepeatRule(weekdays=(1, 3), until=date(2026, 12, 20))
    assert draft.op == "create" and draft.event_id is None
    assert result.data["draft"] is True
    assert result.data["repeat_label"] == "12월 20일까지 매주 월, 수"
    assert result.data["occurrence_count"] == 12
    assert result.data["starts_at"] == "2026-11-09T08:30:00+09:00"


async def test_repeat_draft_goes_out_as_a_repeat_payload(context):
    await _create(context, items=["물통"])

    payload = context.drafts.all()[0].to_payload()

    assert payload["repeat"] == {
        "weekdays": [1, 3],
        "until": "2026-12-20",
        "label": "12월 20일까지 매주 월, 수",
    }
    assert payload["items"] == [{"item_id": None, "item_name": "물통"}]
    assert CreateRepeatEventDraft.model_validate(payload).model_dump(mode="json") == payload


async def test_plain_event_still_has_no_repeat_key(context):
    result = await execute_tool(
        "create_event",
        {"title": "운동회", "starts_on": "11월 9일", "starts_time": "오전 10시"},
        context,
    )

    assert result.success is True, result.error
    draft = context.drafts.all()[0]
    payload = draft.to_payload()
    assert draft.repeat is None
    assert "repeat" not in payload
    assert CreateEventDraft.model_validate(payload).model_dump(mode="json") == payload


async def test_repeat_does_not_write_the_store_and_makes_a_single_draft(context):
    await _create(context)

    assert await context.store.query_events(child_id=context.child_id) == []
    assert len(context.drafts.all()) == 1


async def test_repeat_event_is_core_even_if_episodic_is_asked(context):
    await _create(context, event_type="episodic")

    assert context.drafts.all()[0].event_type == "core"


async def test_repeat_ends_time_follows_the_first_occurrence(context):
    await _create(context, ends_time="오전 9시")

    draft = context.drafts.all()[0]
    assert draft.starts_at == datetime(2026, 11, 9, 8, 30, tzinfo=KST)
    assert draft.ends_at == datetime(2026, 11, 9, 9, 0, tzinfo=KST)


async def test_all_day_repeat(context):
    await _create(context, starts_time="하루 종일")

    draft = context.drafts.all()[0]
    assert draft.all_day is True
    assert draft.starts_at == datetime(2026, 11, 9, 0, 0, tzinfo=KST)
    assert draft.ends_at is not None and draft.ends_at.date() == date(2026, 11, 9)
    assert draft.to_payload()["repeat"]["until"] == "2026-12-20"


async def test_duplicate_weekdays_are_one_set(context):
    await _create(context, repeat_weekdays=["wed", "mon", "wed"])

    assert context.drafts.all()[0].repeat == RepeatRule(weekdays=(1, 3), until=date(2026, 12, 20))


async def test_start_day_that_is_a_match_is_the_first_occurrence(context):
    await _create(context, starts_on="11월 9일", repeat_until="11월 18일")

    draft = context.drafts.all()[0]
    assert draft.starts_at == datetime(2026, 11, 9, 8, 30, tzinfo=KST)


async def test_repeat_without_until_asks_instead_of_filling_a_default(context):
    result = await execute_tool(
        "create_event",
        {
            "title": "등원",
            "starts_on": "11월 9일",
            "starts_time": "오전 8시 30분",
            "repeat_weekdays": ["mon", "wed"],
        },
        context,
    )

    assert result.success is False
    assert result.error["code"] == ErrorCode.REPEAT_UNTIL_REQUIRED
    assert "끝 날짜" in result.error["message"]
    assert context.drafts.all() == ()


async def test_repeat_over_90_days_asks_for_a_nearer_end(context):
    result = await _create(context, starts_on="11월 9일", repeat_until="2027년 3월 1일")

    assert result.success is False
    assert result.error["code"] == ErrorCode.DATE_UNPARSEABLE
    assert "90일" in result.error["message"]
    assert context.drafts.all() == ()


async def test_repeat_exactly_90_days_is_allowed(context):
    # 11/9 + 89일 = 2027-02-06
    result = await _create(context, starts_on="11월 9일", repeat_until="2027년 2월 6일")

    assert result.success is True, result.error


async def test_repeat_until_before_start_asks(context):
    result = await _create(context, starts_on="11월 9일", repeat_until="11월 8일")

    assert result.success is False
    assert result.error["code"] == ErrorCode.DATE_UNPARSEABLE
    assert context.drafts.all() == ()


async def test_repeat_with_no_matching_day_asks(context):
    result = await _create(
        context, starts_on="11월 9일", repeat_weekdays=["fri"], repeat_until="11월 10일"
    )

    assert result.success is False
    assert result.error["code"] == ErrorCode.DATE_UNPARSEABLE
    assert context.drafts.all() == ()


@pytest.mark.parametrize("weekdays", [None, []])
async def test_repeat_until_without_weekdays_is_rejected(context, weekdays):
    result = await execute_tool(
        "create_event",
        {
            "title": "등원",
            "starts_on": "11월 9일",
            "starts_time": "오전 8시 30분",
            "repeat_until": "12월 20일",
            **({} if weekdays is None else {"repeat_weekdays": weekdays}),
        },
        context,
    )

    assert result.success is False
    assert result.error["code"] == ErrorCode.INVALID_ARGS
    assert context.drafts.all() == ()


async def test_repeat_with_ends_on_is_rejected(context):
    result = await _create(context, ends_on="11월 10일", ends_time="오전 9시")

    assert result.success is False
    assert result.error["code"] == ErrorCode.INVALID_ARGS
    assert context.drafts.all() == ()


async def test_unknown_weekday_name_is_rejected(context):
    result = await _create(context, repeat_weekdays=["monday"])

    assert result.success is False
    assert result.error["code"] == ErrorCode.INVALID_ARGS


async def test_repeat_in_the_past_is_not_made(context):
    result = await _create(context, starts_on="11월 2일", repeat_until="11월 30일")

    assert result.success is False
    assert result.error["code"] == ErrorCode.DATE_UNPARSEABLE
    assert context.drafts.all() == ()


async def test_repeat_90_days_are_counted_from_the_first_occurrence(context):
    # 11/7(토)에서 말했고 첫 회차는 11/9(월)이다. 2027-02-06 은 11/9 + 89일이라 된다
    result = await _create(context, repeat_until="2027년 2월 6일")

    assert result.success is True, result.error


async def test_repeat_is_refused_when_the_store_cannot_hold_a_series(context):
    # DB 어댑터가 반복 묶음을 구현하기 전에는 제출할 곳이 없다. 반복으로 말한 일정이 하나만
    # 만들어지는 대신 못 만든다고 알린다
    context.store.supports_series = False

    result = await _create(context)

    assert result.success is False
    assert result.error["code"] == ErrorCode.OUT_OF_SCOPE
    assert context.drafts.all() == ()


async def test_a_plain_event_does_not_need_series_support(context):
    context.store.supports_series = False

    result = await execute_tool(
        "create_event",
        {"title": "운동회", "starts_on": "11월 9일", "starts_time": "오전 10시"},
        context,
    )

    assert result.success is True, result.error


async def _seed_sports_day(context: AgentContext) -> None:
    await context.store.create_event(
        child_id=context.child_id,
        title="운동회",
        starts_at=datetime(2026, 11, 20, 10, 0, tzinfo=KST),
        ends_at=None,
        all_day=False,
        fields={},
    )


async def test_absolute_end_date_works_while_the_start_is_counted_from_an_event(context):
    # "운동회 다음 날부터 12월 20일까지": 기준 일정은 시작 날짜를 세는 데만 쓴다.
    # 끝 날짜까지 기준에서 세려 하면 절대 날짜가 거절된다
    await _seed_sports_day(context)

    result = await _create(
        context,
        starts_on="다음날",
        anchor_event="운동회",
        repeat_weekdays=["mon"],
        repeat_until="12월 20일",
    )

    assert result.success is True, result.error
    draft = context.drafts.all()[0]
    assert draft.starts_at == datetime(2026, 11, 23, 8, 30, tzinfo=KST)  # 11/21(토) 다음 월요일
    assert draft.repeat.until == date(2026, 12, 20)


async def test_relative_end_date_is_counted_from_the_same_anchor(context):
    await _seed_sports_day(context)

    result = await _create(
        context,
        starts_on="다음날",
        anchor_event="운동회",
        repeat_weekdays=["mon"],
        repeat_until="사흘 뒤",
    )

    assert result.success is True, result.error
    assert context.drafts.all()[0].repeat.until == date(2026, 11, 23)  # 운동회 11/20 + 3일
