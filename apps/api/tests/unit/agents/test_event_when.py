"""일정 시간 구간(EventWhen) 회귀 테스트.

시작·종료·all_day 변경 시 시간 구간의 일관성을 검증한다.

    - 종일 일정 날짜 변경: 종료 날짜도 함께 이동
    - 종일 -> 시각 일정 전환: 종일 일정의 종료 시각 제거
    - 종료 < 시작: 잘못된 시간 구간 방지
    - 시각 일정 날짜 변경: 종료 날짜도 함께 이동
    - 생성 시 종료 < 시작: 잘못된 시간 구간 방지
    - 종료 날짜만 변경: 기존 종료 시각 유지
    - 빈 시각 표현: 종일 일정으로 해석하지 않음
    - 생성 시 끝나는 날짜 지정: 자정 넘김/여러 날 일정을 한 번에 저장

리뷰에서 추가로 발견된 사항:

    - 여러 날 일정의 시작 시각 변경: 종료가 함께 밀림
    - 종일 일정에 종료 시각: 조용히 버리고 성공을 돌려줌
    - 끝나는 날짜만 지정: 종료를 시작 시각으로 채움
    - 종료 날짜 해석 기준: 시작일이 아니라 오늘 기준으로 풀림
    - 종료 == 시작: 길이 0 일정이 저장됨

날짜 계산과 일정 시간 구간의 일관성은 모델이 아닌 코드에서 보장하는 영역이다.
"""

from datetime import datetime
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest

from app.agents.memory.context import AgentContext
from app.agents.memory.registry import execute_tool
from app.agents.memory.result import ErrorCode
from app.agents.memory.store import InMemoryStore
from app.agents.memory.store.ports import EventRow

KST = ZoneInfo("Asia/Seoul")
NOW = datetime(2026, 9, 15, 9, 0, tzinfo=KST)  # 2026-09-15 화요일


@pytest.fixture
def context() -> AgentContext:
    return AgentContext(
        child_id=UUID(int=1),
        source_writer=UUID(int=2),
        now=NOW,
        timezone=KST,
        store=InMemoryStore(now=NOW),
    )


async def _create(context: AgentContext, **args: Any) -> str:
    result = await execute_tool("create_event", {"title": "운동회", **args}, context)
    assert result.success is True, result.error
    return result.data["id"]


async def _row(context: AgentContext, event_id: str) -> EventRow:
    row = await context.store.get_event(event_id=event_id)
    assert row is not None
    return row


def _local(moment: datetime | None) -> str | None:
    return moment.astimezone(KST).isoformat(timespec="minutes") if moment else None


async def test_종일_일정의_날짜만_바꾸면_종료도_함께_옮겨간다(context: AgentContext) -> None:
    # "운동회를 다음 날로 옮겨줘": 구간도 통째로 옮겨가야 함
    event_id = await _create(context, starts_on="2026-09-17", starts_time="하루 종일")

    await execute_tool("update_event", {"event_id": event_id, "starts_on": "2026-09-18"}, context)

    row = await _row(context, event_id)
    assert row.all_day is True
    assert _local(row.starts_at) == "2026-09-18T00:00+09:00"
    assert _local(row.ends_at) == "2026-09-18T23:59+09:00"  # 9/17 23:59 에 남아 있었다


async def test_종일에서_시각_일정으로_바꾸면_23시_59분_설정도_사라져야_한다(
    context: AgentContext,
) -> None:
    event_id = await _create(context, starts_on="2026-09-17", starts_time="하루 종일")

    await execute_tool("update_event", {"event_id": event_id, "starts_time": "오전 9시"}, context)

    row = await _row(context, event_id)
    assert row.all_day is False
    assert _local(row.starts_at) == "2026-09-17T09:00+09:00"
    assert row.ends_at is None


async def test_종료가_시작보다_앞서면_고치지_않고_되묻게_한다(context: AgentContext) -> None:
    event_id = await _create(context, starts_on="2026-09-17", starts_time="오후 3시")

    result = await execute_tool(
        "update_event", {"event_id": event_id, "ends_time": "오전 10시"}, context
    )

    assert result.success is False
    assert result.error is not None
    assert result.error["code"] == ErrorCode.VALIDATION_ERROR
    row = await _row(context, event_id)
    assert row.ends_at is None  # 실패한 수정이 일정에 남지 않는다


async def test_시각_있는_일정의_날짜만_바꾸면_종료도_길이를_유지한_채_따라온다(
    context: AgentContext,
) -> None:
    # 시작만 옮기고 종료를 동일하게 두지 않게 하기
    event_id = await _create(
        context, starts_on="2026-09-17", starts_time="오후 3시", ends_time="오후 5시"
    )

    await execute_tool("update_event", {"event_id": event_id, "starts_on": "2026-09-18"}, context)

    row = await _row(context, event_id)
    assert _local(row.starts_at) == "2026-09-18T15:00+09:00"
    assert _local(row.ends_at) == "2026-09-18T17:00+09:00"


async def test_만들_때도_종료가_시작보다_앞서면_저장하지_않는다(context: AgentContext) -> None:
    result = await execute_tool(
        "create_event",
        {
            "title": "소풍",
            "starts_on": "2026-09-17",
            "starts_time": "오후 3시",
            "ends_time": "오전 10시",
        },
        context,
    )

    assert result.success is False
    assert result.error is not None
    assert result.error["code"] == ErrorCode.VALIDATION_ERROR
    assert await context.store.query_events(child_id=context.child_id) == []


async def test_종료_날짜만_바꾸면_종료_시각은_유지된다(context: AgentContext) -> None:
    event_id = await _create(
        context, starts_on="2026-09-17", starts_time="오전 9시", ends_time="오후 5시"
    )

    await execute_tool("update_event", {"event_id": event_id, "ends_on": "2026-09-19"}, context)

    row = await _row(context, event_id)
    assert _local(row.ends_at) == "2026-09-19T17:00+09:00"


async def test_빈_시각_표현은_하루_종일이_아니다(context: AgentContext) -> None:
    event_id = await _create(context, starts_on="2026-09-17", starts_time="오후 3시")

    result = await execute_tool(
        "update_event", {"event_id": event_id, "starts_time": "  "}, context
    )

    assert result.success is False
    assert result.error is not None
    assert result.error["code"] == ErrorCode.DATE_UNPARSEABLE
    assert "몇 시인지" in result.error["message"]
    row = await _row(context, event_id)
    assert row.all_day is False
    assert _local(row.starts_at) == "2026-09-17T15:00+09:00"


async def test_끝나는_날짜를_주면_여러_날_일정도_한_번에_만든다(context: AgentContext) -> None:
    event_id = await _create(
        context,
        starts_on="2026-09-17",
        starts_time="오전 9시",
        ends_on="2026-09-19",
        ends_time="오후 5시",
    )

    row = await _row(context, event_id)
    assert _local(row.starts_at) == "2026-09-17T09:00+09:00"
    assert _local(row.ends_at) == "2026-09-19T17:00+09:00"


async def test_자정을_넘기는_일정도_한_번에_만든다(context: AgentContext) -> None:
    # ends_on이 없으면 종료가 같은 날로 계산돼 종료 < 시작이 된다
    event_id = await _create(
        context,
        starts_on="2026-09-17",
        starts_time="밤 11시",
        ends_on="2026-09-18",
        ends_time="새벽 1시",
    )

    row = await _row(context, event_id)
    assert _local(row.starts_at) == "2026-09-17T23:00+09:00"
    assert _local(row.ends_at) == "2026-09-18T01:00+09:00"


# 리뷰 추가 테스트
async def test_당일_일정은_시작_시각을_바꾸면_길이를_유지한다(context: AgentContext) -> None:
    # "3시에서 6시로 바꿔줘" -> 2시간짜리 예약을 통째로 이동
    event_id = await _create(
        context, starts_on="2026-09-17", starts_time="오후 3시", ends_time="오후 5시"
    )

    await execute_tool("update_event", {"event_id": event_id, "starts_time": "오후 6시"}, context)

    row = await _row(context, event_id)
    assert _local(row.starts_at) == "2026-09-17T18:00+09:00"
    assert _local(row.ends_at) == "2026-09-17T20:00+09:00"


async def test_여러_날_일정은_시작_시각을_바꿔도_종료가_밀리지_않는다(
    context: AgentContext,
) -> None:
    event_id = await _create(
        context,
        starts_on="2026-09-17",
        starts_time="오전 9시",
        ends_on="2026-09-19",
        ends_time="오후 5시",
    )

    await execute_tool("update_event", {"event_id": event_id, "starts_time": "오후 3시"}, context)

    row = await _row(context, event_id)
    assert _local(row.starts_at) == "2026-09-17T15:00+09:00"
    assert _local(row.ends_at) == "2026-09-19T17:00+09:00"


async def test_종일_일정에_종료_시각을_주면_반영하지_않는다(context: AgentContext) -> None:
    created = await execute_tool(
        "create_event",
        {
            "title": "마을 축제",
            "starts_on": "2026-09-17",
            "starts_time": "하루 종일",
            "ends_time": "오후 5시",
        },
        context,
    )
    assert created.success is False
    assert created.error is not None
    assert created.error["code"] == ErrorCode.VALIDATION_ERROR

    event_id = await _create(context, starts_on="2026-09-17", starts_time="하루 종일")
    updated = await execute_tool(
        "update_event", {"event_id": event_id, "ends_time": "오후 5시"}, context
    )
    assert updated.success is False
    assert updated.error is not None
    assert updated.error["code"] == ErrorCode.VALIDATION_ERROR


async def test_끝나는_날짜만_주고_시각을_안_주면_되묻는다(context: AgentContext) -> None:
    result = await execute_tool(
        "create_event",
        {
            "title": "캠프",
            "starts_on": "2026-09-17",
            "starts_time": "오전 9시",
            "ends_on": "2026-09-19",
        },
        context,
    )

    assert result.success is False
    assert result.error is not None
    assert await context.store.query_events(child_id=context.child_id) == []


async def test_끝나는_날짜는_오늘이_아니라_시작일을_기준으로_해석한다(
    context: AgentContext,
) -> None:
    event_id = await _create(
        context,
        starts_on="다음주 월요일",
        starts_time="오전 9시",
        ends_on="수요일",
        ends_time="오후 5시",
    )

    row = await _row(context, event_id)
    assert _local(row.starts_at) == "2026-09-21T09:00+09:00"
    assert _local(row.ends_at) == "2026-09-23T17:00+09:00"


async def test_종료_자리의_자정은_다음_날로_읽는다(context: AgentContext) -> None:
    event_id = await _create(
        context, starts_on="2026-09-17", starts_time="밤 11시", ends_time="자정"
    )

    row = await _row(context, event_id)
    assert _local(row.starts_at) == "2026-09-17T23:00+09:00"
    assert _local(row.ends_at) == "2026-09-18T00:00+09:00"


async def test_종료가_시작과_같으면_저장하지_않는다(context: AgentContext) -> None:
    # 길이 0 일정을 따로 허용하지 않고, "종료 없음"은 ends_at=None이 표현한다
    result = await execute_tool(
        "create_event",
        {
            "title": "면담",
            "starts_on": "2026-09-17",
            "starts_time": "오후 3시",
            "ends_time": "오후 3시",
        },
        context,
    )

    assert result.success is False
    assert result.error is not None
    assert result.error["code"] == ErrorCode.VALIDATION_ERROR
    assert await context.store.query_events(child_id=context.child_id) == []


# 불변식
_BASES = {
    "종일": {"starts_on": "2026-09-17", "starts_time": "하루 종일"},
    "시각": {"starts_on": "2026-09-17", "starts_time": "오후 3시", "ends_time": "오후 5시"},
    "다일": {
        "starts_on": "2026-09-17",
        "starts_time": "오전 9시",
        "ends_on": "2026-09-19",
        "ends_time": "오후 5시",
    },
}
_PATCHES = [
    {"starts_on": "2026-09-18"},
    {"starts_time": "오전 9시"},
    {"starts_time": "하루 종일"},
    {"ends_on": "2026-09-19"},
    {"ends_time": "오후 6시"},
    {"starts_on": "2026-09-18", "starts_time": "하루 종일"},
    {"starts_on": "2026-09-20", "ends_on": "2026-09-21", "ends_time": "오후 1시"},
    {"title": "가을 운동회"},
]
_PATCH_IDS = ["+".join(patch) for patch in _PATCHES]


@pytest.mark.parametrize("base", _BASES.values(), ids=list(_BASES))
@pytest.mark.parametrize("patch", _PATCHES, ids=_PATCH_IDS)
async def test_어떤_수정을_해도_종료는_시작보다_앞서지_않는다(
    context: AgentContext, base: dict[str, Any], patch: dict[str, Any]
) -> None:
    event_id = await _create(context, **base)

    await execute_tool("update_event", {"event_id": event_id, **patch}, context)

    row = await _row(context, event_id)
    assert row.ends_at is None or row.starts_at <= row.ends_at


@pytest.mark.parametrize("base", _BASES.values(), ids=list(_BASES))
@pytest.mark.parametrize("patch", _PATCHES, ids=_PATCH_IDS)
async def test_하루_종일_일정은_언제나_00시에_시작해_23시_59분에_끝난다(
    context: AgentContext, base: dict[str, Any], patch: dict[str, Any]
) -> None:
    event_id = await _create(context, **base)

    await execute_tool("update_event", {"event_id": event_id, **patch}, context)

    row = await _row(context, event_id)
    if not row.all_day:
        return
    assert row.starts_at.astimezone(KST).strftime("%H:%M") == "00:00"
    assert row.ends_at is not None
    assert row.ends_at.astimezone(KST).strftime("%H:%M") == "23:59"
