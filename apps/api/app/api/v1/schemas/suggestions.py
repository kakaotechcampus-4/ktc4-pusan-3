"""제안 채택·일정 초안·일정 제출·목록·평가·추천 생성 요청·응답 — #236 · #284."""

from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.api.v1.schemas.common import Ref
from app.core.event_draft import (
    MAX_DRAFT_ITEMS,
    MAX_ITEM_NAME,
    DraftItemPayload,
    EventCategoryValue,
    EventTypeValue,
    Moment,
)

# ── 공통 ──────────────────────────────────────────────────────────────────

AgentValue = Literal["food", "activity", "growth", "health"]
SuggestionFeedbackValue = Literal["child_liked", "child_disliked", "not_acted"]


class EvidenceOut(BaseModel):
    ref: Ref
    label: str
    observed_to: str
    confidence_source: str
    is_stale: bool | None = None
    note: str


class SuggestionOut(BaseModel):
    id: str
    child_id: str
    agent: str
    kind: str
    content: str
    reason: str | None = Field(
        default=None,
        description="현재 문자열. Agent 연결(#323) 시 {why_this, why_now} 객체로 변경 예정",
    )
    status: str
    expires_at: str
    feedback: str | None
    source_refs: list[Ref] = []
    evidence: list[EvidenceOut] = []


# ── 채택 ──────────────────────────────────────────────────────────────────


class ApproveSuggestionsRequest(BaseModel):
    suggestion_ids: list[uuid.UUID] = Field(max_length=20)


class ApproveSuggestionsResponse(BaseModel):
    suggestions: list[SuggestionOut]


# ── 추천 받기 (J — #284 · #249) ──────────────────────────────────────────


class SuggestionsRequest(BaseModel):
    agents: list[AgentValue] = Field(min_length=1, max_length=2, description="최대 2개 (NF-01)")
    run_id: str | None = Field(
        default=None,
        max_length=100,
        description=(
            "04 '후보 보기' 로 들어온 입력 run. 있으면 그 run 의 추천을 돌려주고, 결과를 못 낸 "
            "Agent 만 15분 안에 처음 맥락으로 다시 만든다. 홈 · 04 제안은 없다"
        ),
    )
    prompt_key: str | None = Field(
        default=None, max_length=50, description="홈 추천 버튼의 키. 문장은 서버가 꺼낸다 (#286)"
    )


class DocEvidenceOut(BaseModel):
    """일반 추천의 근거인 문서 행. CC BY 행은 출처 표시가 조건이라 기관 · 제목을 같이 싣는다.

    Food 일반 추천은 고른 메뉴(레시피 카탈로그 행)도 이 모양으로 싣는다(#442) — `title` 은 메뉴
    이름, 출처는 그 레시피를 받아 온 공개 데이터다.
    """

    ref: Ref = Field(
        description=(
            "kind 는 food_doc · activity_doc · growth_doc, "
            "또는 recipe_catalog(Food 일반 추천의 메뉴 근거)"
        )
    )
    title: str
    source_org: str
    source_title: str
    source_url: str | None = None
    note: str = Field(description="이 문서를 어떻게 참고했는지 한 줄")


class GeneralSuggestionOut(BaseModel):
    """또래 기준 일반 추천. 아이 기록이 아니라 문서 행을 근거로 단다 (루트 §2).

    Food 는 고른 메뉴(레시피 카탈로그 행)만으로도 된다(#442).
    """

    id: str
    child_id: str
    agent: str
    kind: Literal["general"] = "general"
    content: str
    basis: str = Field(description="코드 템플릿 문구. 모델이 쓴 문장이 아니다")
    status: str
    expires_at: str
    record_count: int = Field(
        description=(
            "그 Agent 도메인에 쌓인 기록 수 (남아 있는 관찰만). 04 블록의 record_count 와 같은 표"
        )
    )
    evidence: list[DocEvidenceOut]


class GroupNoticeOut(BaseModel):
    code: Literal["fewer", "empty", "cannot_resume", "daily_limit"] = Field(
        description=(
            "fewer 1~2개 · empty 0개 · cannot_resume 이어서 할 수 없음 · "
            "daily_limit 오늘 한도라 이 Agent 는 돌리지 않음. "
            "화면은 notice 가 있는 묶음에 다시 시도를 띄우지 않는다"
        )
    )
    message: str


class SuggestionGroupOut(BaseModel):
    agent: str
    prompt: str | None = Field(default=None, description="무엇을 봤는지. 아직 비어 있다 (후속)")
    merges_into_one: bool | None = Field(
        default=None, description="채택하면 일정 초안이 한 건으로 묶이는 Agent"
    )
    notice: GroupNoticeOut | None = None
    cautions: list[str] = Field(
        default_factory=list,
        description=(
            "식품 사전에 없는 알레르기(꽃가루 등)의 확인 문구. 코드 문구다 — 화면이 그대로 그린다. "
            "동의가 없으면 늘 빈 목록"
        ),
    )


class GuardOut(BaseModel):
    code: str
    blocked_agents: list[AgentValue]
    message: str
    deeplink: str | None = None


class ScarcityQuestionOut(BaseModel):
    id: str
    text: str
    options: list[str] = []


class ScarcityOut(BaseModel):
    count: int = Field(description="그 Agent 도메인에 쌓인 기록 건수 (남아 있는 관찰만)")
    question: ScarcityQuestionOut | None = Field(
        default=None, description="Agent 가 되물은 한 줄. 없으면 null — 최대 1개"
    )


class PrecheckOut(BaseModel):
    code: str
    item: str
    note: str
    suggestion_id: str


class SuggestionsResponse(BaseModel):
    suggestions: list[SuggestionOut] = Field(
        description="개인화 추천. Agent 마다 0~3개 — 묶음은 groups 의 agent 로 가른다"
    )
    groups: list[SuggestionGroupOut] = Field(
        description="결과를 낸 Agent 마다 하나. 실패한 Agent 는 없다 — 화면이 실패 카드를 그린다"
    )
    general: list[GeneralSuggestionOut] = Field(
        description="또래 기준 일반 추천. 근거는 문서 행이다. Agent 마다 0~3개"
    )
    looked_at: str
    guards: list[GuardOut]
    scarcity: ScarcityOut | None = Field(
        default=None, description="일반 추천이 있을 때만. 쌓인 기록 건수 · 질문은 최대 1개"
    )
    prechecks: list[PrecheckOut] = Field(
        description="채택 전에 물을 알레르기 항목. active 에 걸린 추천은 응답에서 빠진다"
    )


class AnswerRequest(BaseModel):
    question_id: str = Field(max_length=100)
    answer: str = Field(max_length=500)


class AnswerResponse(BaseModel):
    saved: bool


# ── 추천 목록 (I — #284) ─────────────────────────────────────────────────


class SuggestionListResponse(BaseModel):
    items: list[SuggestionOut]
    next_cursor: str | None = None


# ── 추천 평가 (I — #284) ─────────────────────────────────────────────────


class SuggestionFeedbackRequest(BaseModel):
    feedback: SuggestionFeedbackValue


class SuggestionFeedbackResponse(BaseModel):
    suggestion: SuggestionOut
    memory_changed: bool = False


# ── 일정 수정 (I — #284) ─────────────────────────────────────────────────


class UpdateEventBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(max_length=200)
    starts_at: Moment = Field(description="ISO datetime with timezone")
    ends_at: Moment | None = None
    all_day: bool = False
    event_type: EventTypeValue
    category: EventCategoryValue

    @model_validator(mode="after")
    def _ends_after_starts(self) -> UpdateEventBody:
        if self.ends_at is not None and self.ends_at <= self.starts_at:
            msg = "끝나는 시각이 시작보다 늦어야 해요"
            raise ValueError(msg)
        return self


class UpdateEventRequest(BaseModel):
    event: UpdateEventBody
    items: list[DraftItemPayload] = Field(default=[], max_length=MAX_DRAFT_ITEMS)

    @model_validator(mode="after")
    def _item_name_length(self) -> UpdateEventRequest:
        for item in self.items:
            if len(item.item_name) > MAX_ITEM_NAME:
                msg = f"준비물 이름은 {MAX_ITEM_NAME}자까지예요"
                raise ValueError(msg)
        return self


# ── 일정 초안 ─────────────────────────────────────────────────────────────


class CreateEventDraftsRequest(BaseModel):
    suggestion_ids: list[uuid.UUID] = Field(max_length=20)


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

    title: str = Field(max_length=200)
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
    items: list[DraftItemPayload] = Field(default=[], max_length=MAX_DRAFT_ITEMS)
    suggestion_ids: list[uuid.UUID] | None = Field(default=None, max_length=20)

    @model_validator(mode="after")
    def _item_name_length(self) -> SubmitEventRequest:
        for item in self.items:
            if len(item.item_name) > MAX_ITEM_NAME:
                msg = f"준비물 이름은 {MAX_ITEM_NAME}자까지예요"
                raise ValueError(msg)
        return self


class EventItemOut(BaseModel):
    item_id: str
    item_name: str
    is_prepared: bool
    prepared_at: str | None = None


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
