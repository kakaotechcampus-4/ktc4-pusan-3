"""제안 채택·일정 초안·일정 제출·목록·평가·추천 생성 요청·응답 — #236 · #284."""

from __future__ import annotations

import uuid
from datetime import date, time
from typing import Any, Literal

from pydantic import ConfigDict, Field, field_validator, model_validator

from app.api.schema_base import ApiSchema
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
from app.rules.recurrence import repeat_label

# ── 공통 ──────────────────────────────────────────────────────────────────

AgentValue = Literal["food", "activity", "growth", "health"]
SuggestionFeedbackValue = Literal["child_liked", "child_disliked", "not_acted"]


class EvidenceOut(ApiSchema):
    ref: Ref
    label: str
    observed_to: str
    confidence_source: str
    is_stale: bool | None = None
    note: str


class SuggestionOut(ApiSchema):
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
    items: list[str] = Field(
        default_factory=list,
        description=(
            "해 보는 데 필요한 것. food 는 메뉴의 주재료(양념 · 부재료는 뺀다, #474)라 "
            "전체 재료가 아니다 — 알레르기를 확인하는 목록으로 쓰지 않는다"
            "(알레르기는 이 칸과 따로 서버가 거른다). "
            'activity · growth 는 준비물이고 growth 도서는 "제목(저자)" 한 항목이다. '
            "승인한 추천으로 만드는 일정 초안의 준비물(event-drafts 의 items)과 같은 원본이다. "
            "근거가 아니어서 evidence 에 들어가지 않는다. 없으면 빈 배열"
        ),
    )
    event_starts_on: date | None = Field(
        default=None,
        description=(
            "행사가 붙은 추천에만 오는 행사 시작일(YYYY-MM-DD). 화면이 place_notice 줄에 기간으로 "
            "세운다. 근거가 아니다. 행사가 없으면 null"
        ),
    )
    event_ends_on: date | None = Field(
        default=None,
        description="행사가 붙은 추천에만 오는 행사 종료일(YYYY-MM-DD). 행사가 없으면 null",
    )
    reason: str | None = Field(
        default=None,
        description=(
            "왜 이 추천인지 한두 문장. 한 칸이다 — `{why_this, why_now}` 로 나누지 않는다 "
            "(Agent 공통규약 C-2). 일반 추천이면 05 의 basis 와 같은 문구. 없으면 null"
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


class ApproveSuggestionsRequest(ApiSchema):
    suggestion_ids: list[uuid.UUID] = Field(max_length=20)


class ApproveSuggestionsResponse(ApiSchema):
    suggestions: list[SuggestionOut]


# ── 추천 받기 (J — #284 · #249) ──────────────────────────────────────────


class SuggestionsRequest(ApiSchema):
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


class DocEvidenceOut(ApiSchema):
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
    source_title: str = Field(
        description=(
            "원문 제목. 라이선스가 요구하는 표시 문구와 URL 이 제목 뒤에 붙을 수 있다 "
            "(CC BY 의 변경 표시 · OGL 의 출처 문장)"
        )
    )
    source_url: str | None = None
    note: str = Field(description="이 문서를 어떻게 참고했는지 한 줄")


class GeneralSuggestionOut(ApiSchema):
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
    items: list[str] = Field(
        default_factory=list,
        description=(
            "해 보는 데 필요한 것. food 는 메뉴의 주재료(양념 · 부재료는 뺀다, #474)라 "
            "전체 재료가 아니다 — 알레르기를 확인하는 목록으로 쓰지 않는다"
            "(알레르기는 이 칸과 따로 서버가 거른다). "
            'activity · growth 는 준비물이고 growth 도서는 "제목(저자)" 한 항목이다. '
            "승인한 추천으로 만드는 일정 초안의 준비물(event-drafts 의 items)과 같은 원본이다. "
            "근거가 아니어서 evidence 에 들어가지 않는다. 없으면 빈 배열"
        ),
    )
    event_starts_on: date | None = Field(
        default=None,
        description=(
            "행사가 붙은 추천에만 오는 행사 시작일(YYYY-MM-DD). 화면이 place_notice 줄에 기간으로 "
            "세운다. 근거가 아니다. 행사가 없으면 null"
        ),
    )
    event_ends_on: date | None = Field(
        default=None,
        description="행사가 붙은 추천에만 오는 행사 종료일(YYYY-MM-DD). 행사가 없으면 null",
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


class GroupNoticeOut(ApiSchema):
    code: Literal["fewer", "empty", "cannot_resume", "daily_limit"] = Field(
        description=(
            "fewer 1~2개 · empty 0개 · cannot_resume 이어서 할 수 없음 · "
            "daily_limit 오늘 한도라 이 Agent 는 돌리지 않음. "
            "화면은 notice 가 있는 묶음에 다시 시도를 띄우지 않는다"
        )
    )
    message: str


class SuggestionGroupOut(ApiSchema):
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


class GuardOut(ApiSchema):
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


class ScarcityQuestionOut(ApiSchema):
    id: str
    text: str
    options: list[str] = []


class ScarcityOut(ApiSchema):
    count: int = Field(
        description=(
            "일반 추천을 낸 Agent별 record_count의 합(남아 있는 관찰만). 호환 필드이며 화면의 "
            "정상 일반 추천 문구는 general[].record_count를 Agent별로 쓴다"
        )
    )
    question: ScarcityQuestionOut | None = Field(
        default=None, description="Agent 가 되물은 한 줄. 없으면 null — 최대 1개"
    )


class PrecheckOut(ApiSchema):
    code: str
    item: str
    note: str
    suggestion_id: str


class SuggestionsResponse(ApiSchema):
    suggestions: list[SuggestionOut] = Field(
        description="개인화 추천. Agent 마다 0~3개 — 묶음은 groups 의 agent 로 가른다"
    )
    groups: list[SuggestionGroupOut] = Field(
        description="결과를 낸 Agent 마다 하나. 실패한 Agent 는 없다 — 화면이 실패 카드를 그린다"
    )
    general: list[GeneralSuggestionOut] = Field(
        description="일반 추천. 근거는 문서 행이다. Agent 마다 0~3개"
    )
    looked_at: str
    guards: list[GuardOut]
    scarcity: ScarcityOut | None = Field(
        default=None,
        description="일반 추천이 있을 때만. Agent별 기록 수 합계인 호환 필드 · 질문은 최대 1개",
    )
    prechecks: list[PrecheckOut] = Field(
        description="채택 전에 물을 알레르기 항목. active 에 걸린 추천은 응답에서 빠진다"
    )


class AnswerRequest(ApiSchema):
    question_id: str = Field(max_length=100)
    answer: str = Field(max_length=500)


class AnswerResponse(ApiSchema):
    saved: bool


# ── 추천 목록 (I — #284) ─────────────────────────────────────────────────


class SuggestionListResponse(ApiSchema):
    items: list[SuggestionOut]
    total: int = Field(description="status · rated 필터를 건 뒤의 전체 건수. 쪽과 무관하다")
    page: int = Field(description="요청한 쪽 번호 (1부터)")
    size: int = Field(description="요청한 한 쪽 개수")
    total_pages: int = Field(description="전체 쪽 수 — ceil(total / size). 0건이면 0")


# ── 추천 평가 (I — #284) ─────────────────────────────────────────────────


class SuggestionFeedbackRequest(ApiSchema):
    feedback: SuggestionFeedbackValue


class SuggestionFeedbackResponse(ApiSchema):
    suggestion: SuggestionOut
    memory_changed: bool = False


# ── 일정 수정 (I — #284) ─────────────────────────────────────────────────


class UpdateEventBody(ApiSchema):
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


class UpdateEventRequest(ApiSchema):
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


class CreateEventDraftsRequest(ApiSchema):
    suggestion_ids: list[uuid.UUID] = Field(max_length=20)


class EventBodyOut(ApiSchema):
    """starts_at 은 null — 제안에서 온 초안은 일자를 모른다."""

    title: str
    starts_at: Moment | None
    ends_at: Moment | None
    all_day: bool
    event_type: EventTypeValue
    category: EventCategoryValue


class EventDraftOut(ApiSchema):
    draft_id: str
    op: Literal["create"] = "create"
    event_id: None = None
    event: EventBodyOut
    items: list[DraftItemPayload]
    suggestion_ids: list[str]


class CreateEventDraftsResponse(ApiSchema):
    drafts: list[EventDraftOut]


# ── 일정 제출 (승인 게이트 ㉠) ────────────────────────────────────────────


class SubmitEventBody(ApiSchema):
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


class RepeatIn(ApiSchema):
    """요일 반복 규칙 (#738). 문구(`label`)는 서버가 만들어 내므로 받지 않는다."""

    model_config = ConfigDict(extra="forbid")

    weekdays: list[int] = Field(min_length=1, max_length=7, description="ISO 요일 (1=월 … 7=일)")
    until: date = Field(description="마지막 날 (그날 포함). 첫 회차부터 90일 안")


class SubmitEventRequest(ApiSchema):
    event: SubmitEventBody
    items: list[DraftItemPayload] = Field(default=[], max_length=MAX_DRAFT_ITEMS)
    suggestion_ids: list[uuid.UUID] | None = Field(default=None, max_length=20)
    reminder: ReminderIn | None = Field(
        default=None,
        description=(
            "제출한 보호자 본인의 알림. 다른 보호자에게는 걸리지 않는다. 기본값은 없음. "
            "반복이면 회차마다 걸고, 울릴 시각이 이미 지난 회차는 건너뛴다"
        ),
    )
    repeat: RepeatIn | None = Field(
        default=None,
        description=(
            "요일 반복 (#738). 있으면 회차마다 일정 · 준비물 · 알림 행을 만든다. "
            "event.starts_at 은 첫 회차이고, 그 시각(KST)을 모든 회차에 쓴다. "
            "한 회차는 하루 안에서 끝난다"
        ),
    )

    @model_validator(mode="after")
    def _item_name_length(self) -> SubmitEventRequest:
        for item in self.items:
            if len(item.item_name) > MAX_ITEM_NAME:
                msg = f"준비물 이름은 {MAX_ITEM_NAME}자까지예요"
                raise ValueError(msg)
        return self


class EventItemOut(ApiSchema):
    item_id: str
    item_name: str
    is_prepared: bool
    prepared_at: str | None = None


class EventSeriesOut(ApiSchema):
    """일정이 속한 반복 묶음 (#738).

    `label` 은 화면이 그대로 그린다 — 화면은 날짜를 계산하지 않는다.
    """

    id: str
    weekdays: list[int] = Field(description="ISO 요일 (1=월 … 7=일)")
    until: date
    label: str = Field(examples=["12월 20일까지 매주 월, 수"])

    @classmethod
    def of(cls, series: Any | None) -> EventSeriesOut | None:
        """`EventSeries` 행에서. 반복이 아니면(None) None."""
        if series is None:
            return None
        return cls(
            id=str(series.id),
            weekdays=list(series.weekdays),
            until=series.until,
            label=repeat_label(series.weekdays, series.until),
        )


class CalendarEventOut(ApiSchema):
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
    # 🚨 반복이 아니면 키 자체를 싣지 않는다 (null 이 아니다) — 웹 `CalendarEvent.series?` 와
    #    맞춘다. 타입에 None 을 넣지 않아 OpenAPI 에도 null 이 없다. 기본값 None 은 검증하지 않는다
    series: EventSeriesOut = Field(  # type: ignore[assignment]
        default=None,
        exclude_if=lambda value: value is None,
        description="반복 묶음의 회차이면 그 묶음. 반복이 아니면 키가 없다",
    )

    @field_validator("series", mode="wrap")
    @classmethod
    def _series_may_be_absent(cls, value: Any, handler: Any) -> EventSeriesOut | None:
        return None if value is None else handler(value)


class SubmitEventResponse(ApiSchema):
    event: CalendarEventOut = Field(description="만든 일정. 반복이면 첫 회차")


# ── 반복 묶음 고치기 (#738, 승인 게이트 ㉠) ────────────────────────────────


class SeriesEventIn(ApiSchema):
    """묶음 고치기에서 바꾸는 값. 날짜는 없다 — 회차 날짜는 반복 규칙이 정한다. 시각은 KST."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(max_length=200)
    starts_time: time | None = Field(description='"HH:MM" (KST). 종일이면 null')
    ends_time: time | None = None
    all_day: bool = False
    event_type: EventTypeValue
    category: EventCategoryValue

    @model_validator(mode="after")
    def _times(self) -> SeriesEventIn:
        # 종일이면 시각은 보지 않는다 (서버가 버린다)
        if self.all_day:
            return self
        if self.starts_time is None:
            raise ValueError("시작 시각이 필요해요")
        if self.ends_time is not None and self.ends_time <= self.starts_time:
            raise ValueError("끝나는 시각이 시작보다 늦어야 해요")
        return self


class SeriesBeforeIn(SeriesEventIn):
    """초안이 만들어질 때의 묶음 (오늘 이후 회차에서 가장 흔한 값).

    서버는 이것과 달라진 칸만 고친다.
    """

    repeat: RepeatIn
    items: list[str] = Field(default=[], max_length=MAX_DRAFT_ITEMS)


class UpdateSeriesRequest(ApiSchema):
    event: SeriesEventIn
    repeat: RepeatIn
    before: SeriesBeforeIn | None = Field(
        description="null 이면 기준을 모르는 것으로 보고 모든 칸을 오늘 이후 회차에 쓴다"
    )
    items: list[str] = Field(
        default=[],
        max_length=MAX_DRAFT_ITEMS,
        description=(
            "준비물 이름의 최종 목록. 회차마다 이름으로 맞추고, 같은 이름은 챙김 표시를 지킨다"
        ),
    )

    @model_validator(mode="after")
    def _item_name_length(self) -> UpdateSeriesRequest:
        if any(len(name) > MAX_ITEM_NAME for name in self.items):
            msg = f"준비물 이름은 {MAX_ITEM_NAME}자까지예요"
            raise ValueError(msg)
        return self


class UpdateSeriesResponse(ApiSchema):
    series: EventSeriesOut
    occurrence_count: int = Field(description="고친 뒤 오늘 이후(오늘 포함) 회차 수")
