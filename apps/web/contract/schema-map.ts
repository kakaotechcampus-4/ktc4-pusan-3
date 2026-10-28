/**
 * 서버 OpenAPI 스키마 ↔ 웹 타입 대응표 (#559).
 *
 * 🚨 **이름으로 짝을 추측하지 않는다.** 서버 `SubmitEventBody` 는 웹 `SubmitEventBody` 가 아니라
 *    웹 `EventDraftFields` 다 — 이름이 같아도 다른 것을 가리킬 수 있어서 짝은 손으로 적는다.
 *
 * 🚨 **서버 스키마는 전부 여기 둘 중 한 곳에 있어야 한다** (`openapi.test.ts` 가 건다). 서버에 스키마가
 *    새로 생기면 웹 타입을 붙이거나, 붙이지 않는 이유를 `NOT_MAPPED` 에 적는다.
 *
 * 웹 쪽은 타입 이름이나 칸 경로다 (`compare.ts` 의 `readWebTypes`) — `Me.children[]` · `ChatReply<type=note>`.
 */

export interface MapEntry {
  /** 웹 타입 이름이나 칸 경로. */
  web: string;
  /**
   * 어긋나도 되는 칸과 그 이유. 🚨 이유 없이 적지 않는다 — 읽는 사람이 고칠 것인지 판단한다.
   * 🚨 **이 웹 타입 하나에만 걸린다.** 서버 스키마 하나를 웹이 둘로 나눠 받으면 짝마다 따로 적는다 —
   *    한쪽의 사정으로 다른 쪽 검사까지 끄지 않는다.
   */
  allow?: Record<string, string>;
}

/** 서버 스키마 하나를 웹이 둘 이상으로 나눠 받으면 배열로 둘 다 적는다. */
export type MapValue = string | MapEntry | Array<string | MapEntry>;

export const SCHEMA_MAP: Record<string, MapValue> = {
  /* ── 로그인 · 계정 ── */
  AuthProvider: "AuthProvider",
  AuthStatusResponse: "AuthStatus",
  SessionResponse: "AuthSession",
  ParentSummary: "AuthSession.parent",
  ConsentRequiredResponse: "AuthSignupPending",
  SignupRequest: "AuthSignupRequest",
  ConsentInput: "AuthSignupRequest.consents[]",
  ExchangeRequest: "AuthExchangeRequest",
  RefreshResponse: "AuthRefreshResponse",
  MeResponse: "Me",
  MeChild: "Me.children[]",
  WithdrawRequest: "WithdrawRequest",
  WithdrawResponse: "WithdrawResponse",

  /* ── 동의 · 약관 ── */
  PolicyResponse: "Policy",
  ConsentScope: "ConsentRequest.scope",
  ConsentAction: "ConsentRequest.action",
  ConsentRequest: "ConsentRequest",
  ConsentResponse: "ConsentResponse",
  ConsentOut: "ConsentResponse.consent",
  ConsentHistoryEntry: "ConsentHistoryEntry",
  ConsentsResponse: "ConsentsResponse",
  NotificationSettings: "NotificationSettings",
  PushDeviceIn: "PushDeviceRequest",
  PushPlatform: "PushPlatform",

  /* ── 아이 · 보호자 · 초대 ── */
  CreateChildRequest: "CreateChildRequest",
  CreateChildResponse: "CreateChildResponse",
  ChildProfile: "ChildProfile",
  UpdateChildRequest: "UpdateChildRequest",
  UpdateChildResponse: "UpdateChildResponse",
  Gender: "Gender",
  ParentChildRelation: "Relation",
  OnboardingRequest: "OnboardingRequest",
  OnboardingResponse: "OnboardingResponse",
  ChildParentOut: "ChildParent",
  ChildParentsResponse: "ChildParentsResponse",
  CreateInviteRequest: "InviteRequest",
  CreateInviteResponse: "InviteResponse",
  InvitePreviewResponse: "InvitePreviewResponse",
  InvitePreviewChild: "InvitePreviewResponse.child",
  InvitePreviewInviter: "InvitePreviewResponse.invited_by",
  AcceptInviteRequest: "InviteAcceptRequest",
  AcceptInviteResponse: "InviteAcceptResponse",

  /* ── 키 · 몸무게 ── */
  GrowthLogOut: "GrowthLog",
  GrowthLogsResponse: "GrowthLogsResponse",
  CreateGrowthLogRequest: "CreateGrowthLogRequest",
  UpdateGrowthLogRequest: "UpdateGrowthLogRequest",
  GrowthLogResponse: ["CreateGrowthLogResponse", "UpdateGrowthLogResponse"],

  /* ── 알레르기 · 건강 ── */
  HealthSafetyListResponse: "HealthSafetyListResponse",
  HealthSafetyOut: "HealthSafety",
  WrittenBy: "HealthSafety.created_by",
  SafetyKind: "CreateHealthSafetyRequest.type",
  SafetySeverity: "CreateHealthSafetyRequest.severity",
  CreateHealthSafetyRequest: "CreateHealthSafetyRequest",
  CreateHealthSafetyResponse: "CreateHealthSafetyResponse",
  UpdateHealthSafetyRequest: "UpdateHealthSafetyRequest",
  UpdateHealthSafetyResponse: "UpdateHealthSafetyResponse",

  /* ── 03 홈 · 04 한 줄 입력 · 지난 대화 ── */
  HomeResponse: "HomeResponse",
  HighlightOut: "HomeResponse.highlight",
  AgentPromptOut: "HomeResponse.agent_prompts[]",
  TodayEventOut: "HomeResponse.today[]<kind=event>",
  CreateInputRequest: "CreateInputRequest",
  CreateInputResponse: "CreateInputResponse",
  ChatDayResponse: "ChatDayResponse",
  ChatTurnOut: "ChatTurnOut",
  NoteReply: "ChatReply<type=note>",
  GuidanceReply: "ChatReply<type=guidance>",
  UnavailableReply: "ChatReply<type=unavailable>",
  SavedOut: "ChatReply<type=saved>",
  EventDraftReply: "ChatReply<type=event_draft>",
  AgentResultOut: "ChatReply<type=agent_result>",
  PartialReply: "ChatReply<type=partial>",
  DoneReply: "ChatReply<type=done>",
  StoredReadout: "AgentReadout",
  SuggestionStateOut: "AgentResultEvent.suggestions[]",
  StoredRef: "StoredRef",

  /* ── 07 기록 · 기억 · 고치기 ── */
  Ref: "Ref",
  ObservationsResponse: "ObservationsResponse",
  ObservationOut: "ObservationPromotable",
  ObservationHealthOut: "ObservationHealth",
  SourceWriterOut: "ObservationPromotable.source_writer",
  ObservationDetailResponse: "ObservationDetailResponse",
  UsedInOut: "ObservationDetailResponse.used_in[]",
  CorrectionOut: "ObservationDetailResponse.corrections[]",
  AffinitiesResponse: "AffinitiesResponse",
  AffinityOut: "Affinity",
  CorrectionRequest: "CorrectionRequest",
  TargetRef: {
    web: "CorrectionRequest.target_ref",
    allow: {
      kind: "웹은 Ref 를 그대로 싣는다. 서버가 받지 않는 건강 기록은 목록 · 상세 · 캘린더에서 빠져서(#259) 고치기 시트까지 오지 않는다",
    },
  },
  CorrectionResponse: "CorrectionResponse",
  CorrectionMade: "CorrectionResponse.correction",
  Cascade: "CorrectionResponse.cascade",

  /* ── 05 제안 · 06 승인 ── */
  SuggestionsRequest: "SuggestionsRequest",
  SuggestionsResponse: "SuggestionsResponse",
  SuggestionGroupOut: "SuggestionGroup",
  GroupNoticeOut: "GroupNotice",
  GuardOut: "Guard",
  ScarcityOut: "Scarcity",
  ScarcityQuestionOut: "Scarcity.question",
  PrecheckOut: "Precheck",
  SuggestionOut: [
    // 07 목록 — 개인화와 채택한 일반 추천이 같이 온다. kind 를 서버 enum 과 그대로 맞춘다
    "ReceivedSuggestion",
    {
      web: "Suggestion",
      allow: {
        kind: "05 의 개인화 칸(SuggestionsResponse.suggestions)이 같은 서버 스키마를 쓴다. 05 의 일반 추천은 general 칸으로 따로 온다 (#545)",
      },
    },
  ],
  EvidenceOut: "Evidence",
  GeneralSuggestionOut: "GeneralSuggestion",
  DocEvidenceOut: "DocEvidence",
  AnswerRequest: "AnswerRequest",
  ApproveSuggestionsRequest: "ApproveSuggestionsRequest",
  ApproveSuggestionsResponse: "ApproveSuggestionsResponse",
  SuggestionListResponse: "SuggestionListResponse",
  SuggestionFeedbackRequest: "SuggestionFeedbackRequest",
  SuggestionFeedbackResponse: "SuggestionFeedbackResponse",
  CreateEventDraftsRequest: "CreateEventDraftsRequest",
  CreateEventDraftsResponse: "CreateEventDraftsResponse",
  EventDraftOut: {
    web: "EventDraft",
    allow: {
      before:
        "웹 타입은 04 SSE 초안(app/core/event_draft.py)과 같이 쓴다. 이 응답은 create 초안만이라 서버가 싣지 않고, 화면은 없으면 비교를 안 그린다 (event-draft-card.tsx)",
      needs_review: "웹이 제안한 칸이고 서버는 아직 보내지 않는다 (types.ts 의 EventDraft 주석)",
      review_reason: "needs_review 와 같다",
    },
  },
  EventBodyOut: "EventDraftFields",
  DraftItemPayload: "EventDraftItem",
  SubmitEventRequest: "SubmitEventBody",
  SubmitEventBody: {
    web: "EventDraftFields",
    allow: {
      starts_at:
        "초안 칸이라 null 일 수 있다. 날짜 · 시각을 채우기 전에는 제출 버튼이 잠긴다 (event-draft-card.tsx 의 canSubmit)",
    },
  },
  SubmitEventResponse: "SubmitEventResponse",
  UpdateEventRequest: {
    web: "SubmitEventBody",
    allow: {
      suggestion_ids:
        "updateEventDraft 가 Omit 으로 빼고 보낸다 (operations.ts). 제안 연결은 create 에서만 생긴다",
    },
  },
  UpdateEventBody: {
    web: "EventDraftFields",
    allow: { starts_at: "SubmitEventBody 와 같다" },
  },

  /* ── 09 캘린더 ── */
  CalendarMonthResponse: "CalendarMonthResponse",
  CalendarDayOut: "CalendarDay",
  CalendarDayResponse: "CalendarDayResponse",
  CalendarEventOut: "CalendarEvent",
  EventItemOut: "EventItem",
  ReminderOut: "Reminder",
  ReminderLead: "ReminderLead",
  ReminderIn: "ReminderRequest",
  ReminderResponse: "ReminderResponse",
  EventItemUpdateRequest: "EventItemUpdateRequest",
  EventItemUpdateResponse: "EventItemUpdateResponse",
  MyDiaryOut: "MyDiary",
  SharedPhotoOut: "SharedPhoto",
  DiaryRequest: "DiaryRequest",

  /* ── 10 설정 › 문제 알리기 ── */
  ReportCategory: "ReportCategory",
  ReportRequest: "ReportRequest",
  ReportOut: "ReportResponse",

  /* ── 오류 봉투 · 화면 오류 보고 ── */
  ClientErrorReport: "ClientErrorReport",
  ErrorEnvelope: "ApiErrorBody",
  ErrorBody: {
    web: "ApiErrorBody.error",
    allow: { detail: "서버가 exclude_none 으로 보내서 null 대신 키가 빠진다 (app/api/errors.py)" },
  },
};

/**
 * 웹 타입을 붙이지 않는 서버 스키마와 그 이유.
 *
 * BE PR 이 웹이 아직 안 쓰는 스키마를 더할 때는 여기에 `"웹 미사용 — FE 후속 #이슈"` 한 줄을 같이 넣는다.
 * FE 는 그 이슈에서 웹 타입을 붙이고 이 줄을 뺀다 (docs/web/mock-and-tests-v1.md §2 · #560 리뷰).
 */
export const NOT_MAPPED: Record<string, string> = {
  HTTPValidationError:
    "FastAPI 가 자동으로 붙이는 422 모양. 서버는 실제로 ErrorEnvelope 로 바꿔 보낸다 (app/api/errors.py · #438)",
  ValidationError: "HTTPValidationError 안쪽",
  InputLocation:
    "웹이 아직 위치를 보내지 않는다. 위치 동의를 묻지 않고 있다 (lib/consent.ts 의 NOT_ASKED_YET)",
  LinkedAffinityOut: "웹이 ObservationOut.affinity 를 읽지 않는다",
  AnswerResponse: "05 가 되묻기 답의 응답 본문을 읽지 않는다",
};
