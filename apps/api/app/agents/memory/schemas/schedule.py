"""event / event_item / reminder 의 tool argument 스키마.

event 4 + event_item 3 + reminder 3 = 10개.
status · created_by · expires_at · child_id는 규칙이 채운다.
"""

from typing import Annotated

from pydantic import Field

from app.agents.memory.schemas.common import (
    DateExpr,
    Direction,
    EventCategory,
    EventType,
    TemporalDirection,
    TimeExpr,
    ToolArgs,
)

EventId = Annotated[str, Field(description="create_event / query_event 가 돌려준 event id")]


class EventCreate(ToolArgs):
    """새 일정. 시각을 말하지 않았으면 starts_time 을 비워 all_day로 저장한다."""

    title: Annotated[str, Field(description="일정 이름. 예: 운동회, 물놀이")]
    starts_on: DateExpr
    starts_time: TimeExpr
    ends_time: TimeExpr
    temporal_direction: Direction
    event_type: Annotated[
        EventType,
        Field(
            default=EventType.EPISODIC,
            description="매일 반복되는 일과면 core, 단발성 일정이면 episodic",
        ),
    ]
    category: Annotated[
        EventCategory,
        Field(
            default=EventCategory.ETC,
            description="기관 행사 institution, 병원 health, 활동 activity",
        ),
    ]


class EventQuery(ToolArgs):
    """일정 조회. 날짜 조건은 시작 시각(starts_at) 기준이다."""

    temporal_direction: Annotated[
        TemporalDirection,
        Field(
            default=TemporalDirection.NEAREST,
            description=(
                "날짜 표현을 과거로 볼지 미래로 볼지. 앞으로의 일정을 찾으면 nearest, "
                "지난 일정을 찾을 때만 past"
            ),
        ),
    ]
    date_from: Annotated[DateExpr | None, Field(default=None, description="조회 시작 날짜 표현")]
    date_to: Annotated[DateExpr | None, Field(default=None, description="조회 종료 날짜 표현")]
    title_query: Annotated[str | None, Field(default=None, description="일정 이름에 포함된 키워드")]


class EventUpdate(ToolArgs):
    event_id: EventId
    title: Annotated[str | None, Field(default=None, description="바꿀 이름")]
    starts_on: Annotated[DateExpr | None, Field(default=None, description="바꿀 시작 날짜 표현")]
    starts_time: TimeExpr
    ends_on: Annotated[DateExpr | None, Field(default=None, description="바꿀 종료 날짜 표현")]
    ends_time: TimeExpr
    temporal_direction: Direction
    event_type: Annotated[EventType | None, Field(default=None, description="바꿀 일정 유형")]
    category: Annotated[EventCategory | None, Field(default=None, description="바꿀 일정 분류")]


class EventRef(ToolArgs):
    event_id: EventId


class EventItemCreate(ToolArgs):
    """준비물 한 개. 여러 개면 item 마다 따로 호출한다."""

    event_id: EventId
    item_name: Annotated[str, Field(description="준비물 이름. 예: 체육복, 수영복")]


class EventItemUpdate(ToolArgs):
    item_id: Annotated[str, Field(description="query_event 결과의 items[].item_id")]
    item_name: Annotated[str | None, Field(default=None, description="바꿀 이름")]
    is_prepared: Annotated[bool | None, Field(default=None, description="준비 완료 여부")]


class EventItemRef(ToolArgs):
    item_id: Annotated[str, Field(description="query_event 결과의 items[].item_id")]


class ReminderCreate(ToolArgs):
    """알림 한 건. 일정 기준 상대 표현이면 offset_days_from_event 를 쓴다.

    "운동회 전날 저녁 8시" 처럼 기준이 일정인 표현은 remind_on 으로 풀 수 없다.
    """

    event_id: EventId
    remind_on: Annotated[
        str | None,
        Field(default=None, description="알림 날짜 표현. 일정 기준 상대 표현이면 비운다"),
    ]
    offset_days_from_event: Annotated[
        int | None,
        Field(default=None, ge=-30, le=30, description="일정 당일 0, 전날 -1, 이틀 전 -2"),
    ]
    remind_time: TimeExpr
    temporal_direction: Direction


class ReminderUpdate(ToolArgs):
    reminder_id: Annotated[str, Field(description="query_event 결과의 reminders[].id")]
    remind_on: Annotated[str | None, Field(default=None, description="바꿀 날짜 표현")]
    offset_days_from_event: Annotated[
        int | None,
        Field(default=None, ge=-30, le=30, description="일정 기준 상대 일수로 바꿀 때"),
    ]
    remind_time: TimeExpr
    temporal_direction: Direction


class ReminderRef(ToolArgs):
    reminder_id: Annotated[str, Field(description="query_event 결과의 reminders[].id")]
