"""event / event_item 의 tool argument 스키마.

event 4 + event_item 3 = 7개.
created_by · child_id는 규칙이 채운다.

알림은 Agent가 만들지 않는다. 발송은 등록된 일정을 기준으로 자동이다.
"""

from typing import Annotated, ClassVar

from pydantic import Field, field_validator

from app.agents.memory.schemas.common import (
    ClearableUpdateArgs,
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
    """새 일정. 시작 시각이 있어야 초안을 만든다. 모르면 부르지 말고 보호자에게 묻는다."""

    title: Annotated[str, Field(description="일정 이름. 예: 운동회, 물놀이")]
    starts_on: DateExpr
    starts_time: Annotated[
        str,
        Field(
            description=(
                "시작 시각. 원문 표현 그대로. 예: 오전 10시, 저녁 8시, 14:30. "
                "하루 종일 하는 행사면 '하루 종일'. "
                "발화에 시각도 '하루 종일' 도 없으면 이 tool 을 부르지 말고 몇 시인지 먼저 묻는다"
            )
        ),
    ]
    ends_on: Annotated[
        DateExpr | None,
        Field(
            default=None,
            description=(
                "끝나는 날짜. 시작과 같은 날에 끝나면 비워 둔다. 자정을 넘기거나(밤 11시~새벽 1시) "
                "여러 날 이어지는 일정(17일~19일 캠프)일 때만 넣는다"
            ),
        ),
    ]
    ends_time: Annotated[
        str | None,
        Field(
            default=None,
            description="끝나는 시각. 원문 표현 그대로. 예: 오후 5시, 17:30",
        ),
    ]
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
    items: Annotated[
        list[str],
        Field(
            default_factory=list,
            description=(
                '챙길 준비물 이름. 발화에 나온 준비물을 여기에 다 넣는다. 예: ["체육복", "물통"]. '
                "준비물 얘기가 없으면 비워 둔다"
            ),
        ),
    ]

    @field_validator("items")
    @classmethod
    def _named_items(cls, names: list[str]) -> list[str]:
        cleaned = [name.strip() for name in names]
        if not all(cleaned):
            raise ValueError("준비물 이름이 비어 있다. 이름을 넣거나 items 에서 뺀다")
        return cleaned


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


class EventUpdate(ClearableUpdateArgs):
    # ends_at 하나가 ends_on, ends_time 두 개의 인자로 나뉘어 들어오기 때문에
    # 컬럼째로만 지우게 함
    CLEARABLE: ClassVar[frozenset[str]] = frozenset({"ends_at"})
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
