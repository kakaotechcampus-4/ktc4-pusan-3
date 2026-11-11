"""기관 공지 조회 tool 의 argument 스키마 (#832). 공지는 읽기만 해서 조회 하나뿐이다."""

from typing import Annotated, Literal

from pydantic import Field

from app.agents.memory.schemas.common import (
    AnchorEvent,
    AnchorOn,
    DateExpr,
    TemporalDirection,
    ToolArgs,
)


class NoticeQuery(ToolArgs):
    """공지 조회. 받은 날 · 공지의 날 · 행사 일정 · 마감 중 하나라도 날짜에 걸리면 찾는다."""

    temporal_direction: Annotated[
        TemporalDirection,
        Field(
            default=TemporalDirection.NEAREST,
            description=(
                "날짜 표현을 과거로 볼지 미래로 볼지. 앞으로의 행사 · 마감을 찾으면 nearest, "
                "이미 받은 공지를 찾을 때만 past"
            ),
        ),
    ]
    date_from: Annotated[DateExpr | None, Field(default=None, description="조회 시작 날짜 표현")]
    date_to: Annotated[DateExpr | None, Field(default=None, description="조회 종료 날짜 표현")]
    anchor_event: AnchorEvent
    anchor_on: AnchorOn
    text_query: Annotated[
        str | None,
        Field(default=None, description="공지 제목 · 원문 · 항목 제목에 든 낱말"),
    ]
    doc_kind: Annotated[
        Literal["daily_note", "newsletter", "meal_plan", "schedule", "message", "other"] | None,
        Field(
            default=None,
            description=(
                "알림장만 · 가정통신문만처럼 종류를 말했을 때만. "
                "daily_note 알림장, newsletter 가정통신문, meal_plan 식단표, "
                "schedule 일정표, message 기관 문자, other 기타"
            ),
        ),
    ]
