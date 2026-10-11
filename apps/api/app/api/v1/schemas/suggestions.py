"""제안 채택·일정 초안·일정 제출 요청·응답 — #236."""

from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.api.v1.schemas.common import Ref
from app.core.event_draft import (
    DraftItemPayload,
    EventCategoryValue,
    EventTypeValue,
    Moment,
)

# ── 채택 ──────────────────────────────────────────────────────────────────


class ApproveSuggestionsRequest(BaseModel):
    suggestion_ids: list[uuid.UUID]


class SuggestionOut(BaseModel):
    id: str
    child_id: str
    agent: str
    kind: str
    content: str
    reason: str | None
    status: str
    expires_at: str
    feedback: str | None


class ApproveSuggestionsResponse(BaseModel):
    suggestions: list[SuggestionOut]


# ── 일정 초안 ─────────────────────────────────────────────────────────────


class CreateEventDraftsRequest(BaseModel):
    suggestion_ids: list[uuid.UUID]


class EventBodyOut(BaseModel):
    """starts_at 은 null — 제안에서 온 초안은 일자를 모른다."""

    title: str
    starts_at: Moment | None
    ends_at: Moment | None
    all_day: bool
    event_type: EventTypeValue
    category: EventCategoryValue


class EventDraftOut(BaseModel):
    draft_id: str
    op: Literal["create"] = "create"
    event_id: None = None
    event: EventBodyOut
    items: list[DraftItemPayload]
    suggestion_ids: list[str]


class CreateEventDraftsResponse(BaseModel):
    drafts: list[EventDraftOut]


# ── 일정 제출 (승인 게이트 ㉠) ────────────────────────────────────────────


class SubmitEventBody(BaseModel):
    """실제 제출 시에는 starts_at 필수."""

    model_config = ConfigDict(extra="forbid")

    title: str
    starts_at: Moment = Field(description="ISO datetime with timezone — 보호자가 채운다")
    ends_at: Moment | None = None
    all_day: bool = False
    event_type: EventTypeValue
    category: EventCategoryValue

    @model_validator(mode="after")
    def _ends_after_starts(self) -> SubmitEventBody:
        if self.ends_at is not None and self.ends_at <= self.starts_at:
            msg = "끝나는 시각이 시작보다 늦어야 해요"
            raise ValueError(msg)
        return self


class SubmitEventRequest(BaseModel):
    event: SubmitEventBody
    items: list[DraftItemPayload] = []
    suggestion_ids: list[uuid.UUID] | None = None


class EventItemOut(BaseModel):
    item_id: str
    item_name: str
    is_prepared: bool


class CalendarEventOut(BaseModel):
    id: str
    title: str
    event_type: str
    starts_at: str
    ends_at: str | None
    all_day: bool
    category: str
    created_by: str
    source_notice_id: str | None
    source_refs: list[Ref]
    items: list[EventItemOut]
    reminders: list = []


class SubmitEventResponse(BaseModel):
    event: CalendarEventOut
