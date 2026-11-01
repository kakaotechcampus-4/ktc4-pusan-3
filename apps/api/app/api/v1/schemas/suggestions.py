"""제안 채택·일정 초안·일정 제출·목록·평가·추천 생성 요청·응답 — #236 · #284."""

from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.api.v1.schemas.common import Ref
from app.api.v1.schemas.reminders import ReminderIn, ReminderOut
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
    kind: Literal["personalized", "general"] = Field(
        description=(
            "개인화(아이 기록 근거) · 일반(또래 기준) 구분. 07 목록은 둘을 함께 돌려준다 (#532)"
        )
    )
    content: str
    place_notice: str | None = Field(
        default=None,
        description=(
            "장소가 붙은 추천에만 오는 확인 문구 한 문장(공공데이터 기준일 · 운영 여부 확인). "
            "서버 코드 문구다. 근거가 아니어서 evidence 에 들어가지 않는다. 장소가 없으면 null"
        ),
    )
    warnings: list[str] = Field(
        default_factory=list,
        description=(
            "그 추천에 주의할 요소가 있을 때만 오는 위험 경고 문구(예: 작은 부품 · 물놀이). "
            "서버 코드 문구라 글자 그대로 보여 준다. 근거가 아니어서 evidence 에 들어가지 않는다. "
            "없으면 빈 배열"
        ),
    )
    reason: str | None = Field(
        default=None,
        description=(
            "현재 문자열. Agent 연결(#323) 시 {why_this, why_now} 객체로 변경 예정. "
            "일반 추천이면 05 의 basis 와 같은 문구"
        ),
    )
    status: str
    expires_at: str
    feedback: str | None
    source_refs: list[Ref] = Field(
        default=[],
        description="evidence 의 ref 만 모은 것. evidence 와 같이 비는 조건이 같다",
    )
    evidence: list[EvidenceOut] = Field(
        default=[],
        description=(
            "아이 기록 근거만 싣는다. 일반 추천이면 늘 빈 배열이고, "
            "채택 응답은 근거를 읽지 않아 개인화도 빈 배열이다"
        ),
    )


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
    이름, 출처는 그 레시피를 받아 온 공개 데이터다. Growth 일반 도서 추천도 고른
    책(`book_catalog`)을 싣는다 — `title` 은 그 추천의 "제목(저자)", 출처는 도서관 정보나루다.
    """

    ref: Ref = Field(
        description=(
            "kind 는 food_doc · activity_doc · growth_doc, 또는 recipe_catalog(Food 일반 추천의 "
            "메뉴 근거) · book_catalog(Growth 일반 도서 추천의 책 근거)"
        )
    )
    title: str
    source_org: str
    source_title: str
    source_url: str | None = None
    note: str = Field(description="이 문서를 어떻게 참고했는지 한 줄")


class GeneralSuggestionOut(BaseModel):
    """일반 추천. 아이 기록이 아니라 문서 행을 근거로 단다 (루트 §2).

    Food 는 고른 메뉴(레시피 카탈로그 행)만으로도 된다(#442). Growth 도서는 고른 책(정보나루
    서지 행)이 근거다. 보호자가 놀이를 정해 물은 Activity 추천("물놀이 뭐 하지?")은 그 요청이
    기준이라 `evidence` 가 비어 올 수 있다 — 그때 기준은 `basis` 문구다 (#597).
    """

    id: str
    child_id: str
    agent: str
    kind: Literal["general"] = "general"
    content: str
    place_notice: str | None = Field(
        default=None,
        description=(
            "장소가 붙은 추천에만 오는 확인 문구 한 문장(공공데이터 기준일 · 운영 여부 확인). "
            "서버 코드 문구다. 근거가 아니어서 evidence 에 들어가지 않는다. 장소가 없으면 null"
        ),
    )
    warnings: list[str] = Field(
        default_factory=list,
        description=(
            "그 추천에 주의할 요소가 있을 때만 오는 위험 경고 문구(예: 작은 부품 · 물놀이). "
            "서버 코드 문구라 글자 그대로 보여 준다. 근거가 아니어서 evidence 에 들어가지 않는다. "
            "없으면 빈 배열"
        ),
    )
    basis: str = Field(description="코드 템플릿 문구. 모델이 쓴 문장이 아니다")
    status: str
    expires_at: str
    record_count: int = Field(
        description=(
            "그 Agent 도메인에 쌓인 기록 수 (남아 있는 관찰만). 04 블록의 record_count 와 같은 표"
        )
    )
    evidence: list[DocEvidenceOut] = Field(
        description=(
            "문서 · 메뉴 · 책 근거 카드. 보호자가 놀이를 정해 물은 Activity 추천은 비어 올 수 "
            "있다 — 그때는 basis 만 보여 준다"
        )
    )


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
    deeplink: str | None = Field(
        default=None,
        description=(
            "풀러 갈 곳 — 아이 id 를 안 담은 웹 상대 경로. "
            "safety_unknown 이면 profile(11 아이 프로필), "
            "갈 곳이 없는 guard(not_ready · unsupported)는 null"
        ),
    )


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
    total: int = Field(description="status · rated 필터를 건 뒤의 전체 건수. 쪽과 무관하다")
    page: int = Field(description="요청한 쪽 번호 (1부터)")
    size: int = Field(description="요청한 한 쪽 개수")
    total_pages: int = Field(description="전체 쪽 수 — ceil(total / size). 0건이면 0")


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
    reminder: ReminderIn | None = Field(
        default=None,
        description=(
            "요청한 보호자 본인의 알림. 칸을 빼거나 저장된 값과 같으면 그대로 두고, null 이면 "
            "지운다. 종일 여부를 바꾸면 이 일정에 걸린 알림은 날 수를 지켜 가까운 값으로 옮겨진다"
            "(시간 있는 일정의 1h → 종일의 day_of_9am 등). 옮긴 값의 울릴 시각이 지났으면 지워진다"
        ),
    )

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
    reminder: ReminderIn | None = Field(
        default=None,
        description="제출한 보호자 본인의 알림. 다른 보호자에게는 걸리지 않는다. 기본값은 없음",
    )

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
    reminder: ReminderOut | None = Field(
        default=None,
        description="요청한 보호자 본인의 알림. 다른 보호자의 알림은 싣지 않는다",
    )


class SubmitEventResponse(BaseModel):
    event: CalendarEventOut
