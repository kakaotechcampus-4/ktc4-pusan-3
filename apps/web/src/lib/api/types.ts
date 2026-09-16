/**
 * API 계약서 v1 §02 공통 타입.
 * 정본은 docs/api/api-interface-v1.html — 여기 타입이 계약서와 어긋나면 계약서가 맞다.
 */

/* ── Ref — 도메인 간 참조 ────────────────────────────────────────────────
 * observation 테이블이 4개로 나뉘어 있어 id 만으로는 어느 테이블인지 알 수 없다.
 * 모든 참조는 { kind, id } 모양이다.
 */
export const OBSERVATION_KINDS = [
  "observation_food",
  "observation_health",
  "observation_education",
  "observation_activity",
] as const;
export type ObservationKind = (typeof OBSERVATION_KINDS)[number];

export const REF_KINDS = [
  ...OBSERVATION_KINDS,
  "profile_affinity",
  "health_safety",
  "event",
  "suggestion",
] as const;
export type RefKind = (typeof REF_KINDS)[number];

export interface Ref {
  kind: RefKind;
  id: string;
}

/* ── 도메인 enum ──────────────────────────────────────────────────────── */

/** 🚨 코드에서는 play/interest 가 아니라 activity 로 통일한다 (CLAUDE.md §5). */
export const AGENTS = ["food", "activity", "education", "health"] as const;
export type Agent = (typeof AGENTS)[number];

/** 승격 상태. LLM 이 직접 쓰지 않는다 — Curator 의 반복 집계로만 바뀐다 (CLAUDE.md §2). */
export type AffinityState = "candidate" | "confirmed" | "archived";

/** "맞아요" 교정으로 상향된다. */
export type ConfidenceSource =
  "institution_notice" | "parent_direct" | "parent_hedged" | "parent_hearsay";

/**
 * 교정 판정 (CLAUDE.md §5 Correction).
 *
 * 🚨 **화면이 쓰는 것과 타입에 있는 것이 다르다.** 여기는 계약서 enum 이라 지난 교정 이력
 *    (`ObservationDetailResponse.corrections`)에 실려 오는 값을 전부 담아야 한다. 어떤 버튼을
 *    보여줄지는 `CorrectionButtons` 의 표가 정한다 — 기록과 기억이 서로 다른 것을 묻는다.
 *
 * ⚠️ `need_more_observation` 은 **계약서 v1 에 아직 없다.** "아직 확정하지 말고 더 지켜보자" 는
 *    기억에만 있는 판정인데 v1 에는 그 자리가 없어서(`once_only` 는 관찰 한 건에 대한 말이다)
 *    제안 형태로 두고 목에 먼저 세웠다. 👉 `apps/api` Owner 협의 대상 (최상위 CLAUDE.md §8).
 */
export type CorrectionVerdict =
  "confirm" | "once_only" | "need_more_observation" | "outdated" | "wrong";

export type SuggestionStatus = "draft" | "approved" | "rejected" | "expired";
export type EventStatus = "draft" | "confirmed" | "cancelled";

/* ── Observation ──────────────────────────────────────────────────────── */

interface ObservationBase {
  id: string;
  child_id: string;
  raw_text: string;
  confidence_source: ConfidenceSource;
  status: "active" | "inactive";
  /** observed_range 하한 (YYYY-MM-DD). */
  observed_from: string;
  /** 상한. 무한대 금지. */
  observed_to: string;
  /** "오늘" 같은 표시 문구. 서버가 만든다 — 프론트에서 날짜를 다시 계산하지 않는다. */
  observed_label?: string;
  source_writer?: { parent_id: string; nickname: string };
  source_notice_id?: string | null;
  created_at?: string;
}

/** food · education · activity — 승격 파이프라인 안에 있는 3종. */
export interface ObservationPromotable extends ObservationBase {
  kind: Exclude<ObservationKind, "observation_health">;
  subject: string;
  /** -1 / 0 / 1 */
  polarity: -1 | 0 | 1;
  strong_signals: string[];
  affinity: { id: string; merge_key: string; state: AffinityState } | null;
  domain_fields: Record<string, unknown>;
}

/**
 * 🚨 health 는 모양이 다르다.
 * subject · polarity · strong_signals · affinity 키 자체가 없다 (승격 파이프라인 밖).
 * 프론트는 null 검사가 아니라 kind === "observation_health" 로 분기한다.
 */
export interface ObservationHealth extends ObservationBase {
  kind: "observation_health";
  domain_fields: {
    /** 배열이다. */
    symptom?: string[];
    severity?: string;
    body_part?: string;
    suspected_trigger?: string;
    action_taken?: string;
    observed_time?: string;
    [key: string]: unknown;
  };
}

export type Observation = ObservationPromotable | ObservationHealth;

export function isHealthObservation(o: Observation): o is ObservationHealth {
  return o.kind === "observation_health";
}

/* ── Affinity (Child Memory) ──────────────────────────────────────────── */

export interface Affinity {
  kind: "profile_affinity";
  id: string;
  /** 사람이 읽는 라벨. 수정 가능. */
  merge_key: string;
  domain: Exclude<Agent, "health">;
  state: AffinityState;
  /** confirmed 면 NOT NULL. */
  polarity: -1 | 0 | 1 | null;
  strength: number;
  last_observed_on: string;
  /** 서버 파생값 (affinity_id 역참조 집계). */
  observation_count: number;
  /** 승격 임계값 기준으로 서버가 만든 문구. 프론트에서 만들지 않는다. */
  state_reason: string;
  /** 🚨 6개월 이상 전이면 true — 단독 근거로 쓰지 않는다 (NF-08). */
  is_stale: boolean;
  source_refs: Ref[];
}

/* ── HealthSafety ─────────────────────────────────────────────────────── */

/**
 * 🚨 LLM 이 생성·추론·수정하지 않는다 (NF-03). 보호자 직접 입력 또는 의료 기록만.
 * DB 의 health_safety.kind 는 API 에서 type 으로 내려온다 (Ref 의 kind 와 이름 충돌 회피).
 */
export interface HealthSafety {
  kind: "health_safety";
  id: string;
  type: string;
  label: string;
  aliases: string[];
  category: string;
  severity: string | null;
  reactions: string[];
  management: Record<string, unknown>;
  notes: string | null;
  created_by?: { parent_id: string; nickname: string };
  updated_at: string;
}

/* ── Suggestion ───────────────────────────────────────────────────────── */

export interface Evidence {
  ref: Ref;
  label: string;
  observed_to: string;
  confidence_source: ConfidenceSource;
  /**
   * ⚠️ **계약서 v1 에 아직 없는 필드다.** 6개월 지난 근거는 단독으로 쓰지 않는다는 규칙(NF-08)을
   *    화면이 보여주려면 이 판정이 필요한데, 프론트는 날짜를 계산하지 않으므로(CLAUDE.md §3)
   *    서버가 내려줘야 한다. `Affinity.is_stale` 은 있고 `Evidence` 에는 없다 — 계약서 수정 대상.
   *    서버가 보내기 전까지는 항상 undefined 라서 점선 칩이 나오지 않는다.
   */
  is_stale?: boolean;
}

/**
 * 🚨 evidence 0건은 응답에 실리지 않는다.
 * source_refs 가 빈 suggestion 은 버그라서 서버가 생성 단계에서 버리고 scarcity 로 내린다.
 * "근거 없음" 상태를 화면에 그리지 않는다 — 그 상태는 존재하지 않아야 한다 (CLAUDE.md §2).
 */
export interface Suggestion {
  id: string;
  child_id: string;
  agent: Agent;
  /**
   * 🚨 **개인화와 일반을 가르는 필드다** (CLAUDE.md §2 "타입으로 구분한다").
   *    일반 추천은 근거 0행이 정상이지만 그래서 개인화로 집계되면 안 된다 —
   *    한 필드에 섞이면 "개인화인데 근거 0행이면 버그" 라는 하드 기준이 무의미해진다.
   */
  kind: "personalized";
  content: string;
  reason: { why_this: string; why_now: string };
  status: SuggestionStatus;
  /** 생성 +24h. 승인 없는 draft 는 여기서 만료된다 (NF-07). */
  expires_at: string;
  feedback: unknown | null;
  source_refs: Ref[];
  evidence: Evidence[];
}

/**
 * 또래 기준 일반 추천. 근거 Memory 가 부족할 때 개인화 **대신** 나간다 (CLAUDE.md §2).
 *
 * 🚨 **`Suggestion` 과 별도 타입이고 응답에서도 별도 필드다.** 같은 배열에 플래그로 섞으면
 *    언젠가 근거 0건인 것이 개인화로 그려진다 (디자인 시스템 §7). 여기에는 `evidence` ·
 *    `source_refs` 필드가 **아예 없어서**, 개인화 목록(`SuggestionList`)에 넘기면 타입이 막는다.
 *
 * 🚨 **`basis` 는 서버 문구다.** "또래 기준" 이라는 말을 프론트가 지어내지 않는다 —
 *    무엇을 기준으로 골랐는지는 만든 쪽만 안다.
 */
export interface GeneralSuggestion {
  id: string;
  child_id: string;
  agent: Agent;
  kind: "general";
  content: string;
  /** "36개월 또래가 자주 찾는 놀이예요" 처럼 무엇을 기준으로 골랐는지. 서버가 만든다. */
  basis: string;
}

/* ── Event · EventItem · Reminder ─────────────────────────────────────── */

export interface EventItem {
  item_id: string;
  item_name: string;
  is_prepared: boolean;
  /** 서버가 채운다. */
  prepared_at: string | null;
}

export interface Reminder {
  id: string;
  remind_at: string;
  /** true 면 PATCH·DELETE 모두 409 already_sent. */
  sent: boolean;
}

export interface CalendarEvent {
  id: string;
  title: string;
  event_type: "core" | "episodic";
  starts_at: string;
  ends_at: string | null;
  all_day: boolean;
  category: "institution" | "health" | "activity" | "etc";
  /** draft 는 24h 만료. confirmed 로 가는 길목이 승인 게이트 ㉠ 이다. */
  status: EventStatus;
  created_by: "agent" | "caregiver";
  source_notice_id: string | null;
  source_refs: Ref[];
  items: EventItem[];
  reminders: Reminder[];
}

/* ── 화면 응답 ────────────────────────────────────────────────────────── */

/** 03 홈 화면 한 번에. 기억이 0건이면 highlight 가 null 이고 프론트는 빈 상태를 그린다. */
export interface HomeResponse {
  observation_count: number;
  week_count: number;
  upcoming_count: number;
  today: Array<
    | { kind: "meal"; title: string; origin: string }
    | { kind: "event"; event_id: string; title: string }
  >;
  highlight: { text: string; state_reason: string; ref: Ref } | null;
  /** 시각대 규칙(F-15)으로 서버가 만든다 — 모델을 부르지 않는다. */
  agent_prompts: Array<{ agent: Agent; text: string }>;
}

/* ── 03 홈 · 한 줄 입력 ──────────────────────────────────────────────── */

/**
 * POST /children/{cid}/inputs — 즉시 202 로 run_id 만 온다. 결과는 전부 SSE 로 흐른다.
 * 🚨 Idempotency-Key 가 필수다. 재시도할 때 키를 새로 만들지 않는다.
 */
export interface CreateInputRequest {
  text: string;
  source: "home_input" | "photo" | (string & {});
}

export interface CreateInputResponse {
  run_id: string;
}

/* ── 05 제안 후보 ────────────────────────────────────────────────────── */

/** 🚨 agents 는 최대 2개다 (NF-01). Supervisor 가 고른 것을 그대로 싣는다. */
export interface SuggestionsRequest {
  /** 04 의 offer 에서 넘어온 경우에만 있다. 홈의 agent_prompts 로 들어오면 run 이 없다. */
  run_id?: string;
  agents: Agent[];
}

/**
 * Agent 실행이 규칙에 막힌 것. 🚨 모델이 만드는 값이 아니다 —
 * `safety_unknown` 은 알레르기를 모르면 Food Agent 를 **아예 실행하지 않는다**는 규칙의 결과다.
 */
export interface Guard {
  code: "safety_unknown" | (string & {});
  blocked_agents: Agent[];
  message: string;
  /** 아이를 안 담은 상대 경로다 (`settings/health-safety`). 지금 아이 경로 아래에 붙여 쓴다. */
  deeplink?: string;
}

/**
 * 근거가 부족할 때. 🚨 `question` 은 배열이 아니라 **단수**다 —
 * "질문은 한 개까지만 드려요" 를 타입으로 못 박은 자리다 (CLAUDE.md §2).
 */
export interface Scarcity {
  /** 쌓인 기록 건수. 화면에 그대로 보여준다 — 숨기지 않는다. */
  count: number;
  question: { id: string; text: string; options: string[] };
}

/**
 * ⚠️ **계약서 v1 에 아직 없는 필드다** (`general`). "근거가 부족하면 개인화 대신 일반 추천을 낸다"
 *    (CLAUDE.md §2)를 화면이 지키려면 실을 데이터가 필요한데, v1 의 `scarcity` 응답은
 *    `suggestions: []` 로 끝난다. 필드 없이 프론트가 또래 기준 추천을 지어내면 규칙을 UI 로
 *    덮는 것이라, 서버가 내려주는 형태로 여기 제안해 두고 목으로 먼저 세웠다.
 *    👉 `apps/api` Owner 협의 대상이다 (최상위 CLAUDE.md §8 "영역 간 인터페이스").
 *    서버가 안 보내면 `general` 이 undefined 라 화면은 예전처럼 질문 1개만 그린다.
 */

/**
 * 🚨 `suggestions` 가 비어 있는데 `scarcity` 도 null 인 응답은 없다.
 *    둘 다 비면 화면에 그릴 것이 없다는 뜻이고, 그건 서버 버그다.
 */
export interface SuggestionsResponse {
  /** 🚨 개인화만 담는다. 일반 추천은 아래 `general` 이다 — 한 배열에 섞지 않는다. */
  suggestions: Suggestion[];
  /**
   * 또래 기준 일반 추천. `scarcity` 가 있을 때만 채워진다 (개인화 **대신** 나가는 것이라
   * 둘이 같이 나오지 않는다). 위 ⚠️ 참고 — 계약서에 아직 없어서 optional 이다.
   */
  general?: GeneralSuggestion[];
  /** "오늘 급식 · 최근 3일 식사 · 확정 관심 2건" — 무엇을 봤는지. 서버가 만든 문구다. */
  looked_at: string;
  guards: Guard[];
  scarcity: Scarcity | null;
}

/** 되묻는 질문의 답. 관찰 1건으로 저장된다 (confidence_source: parent_direct). */
export interface AnswerRequest {
  question_id: string;
  answer: string;
}

/* ── 06 승인 ─────────────────────────────────────────────────────────── */

/**
 * POST /suggestions/{sid}/event — 승인 게이트 ㉠ **준비**.
 * 여기서는 아직 캘린더에 쓰지 않는다. draft 만 만들고 24시간 뒤 만료된다 (NF-07).
 *
 * ⚠️ `starts_at` 을 프론트가 만들지 않는다. 날짜를 계산하지 않는다는 규칙(CLAUDE.md §3) 때문에
 *    시각은 서버가 제안에서 정하고, 화면은 응답의 `event.starts_at` 을 표시만 한다.
 *    부모가 시각을 직접 고르는 경로는 09 캘린더 화면 것이다.
 */
export interface CreateEventRequest {
  title: string;
  items?: string[];
}

/**
 * 06 화면 상단 "확인해 주세요" 배너. 🚨 규칙이 만들고 모델은 관여하지 않는다.
 * `unknown_ingredient` = 알레르기 기록에 없는 재료 — 보호자에게 물어야 한다.
 */
export interface Precheck {
  code: "unknown_ingredient" | (string & {});
  item: string;
  note: string;
}

export interface CreateEventResponse {
  event: CalendarEvent;
  /** draft 만료 시각. 지나면 승인할 수 없다. */
  expires_at: string;
  prechecks: Precheck[];
}

/** 🚨 승인 게이트 ㉠ — 되돌릴 수 없는 지점. 이미 확정이면 409 already_confirmed. */
export interface ConfirmEventResponse {
  event: CalendarEvent;
  suggestion_status: SuggestionStatus;
}

/**
 * 🚨 승인 게이트 ㉡ — 알레르기·만성질환의 **유일한 쓰기 경로**다.
 *    보호자가 확인한 값만 들어간다. LLM 이 추론한 값을 여기에 싣지 않는다 (NF-03).
 */
export interface CreateHealthSafetyRequest {
  type: string;
  label: string;
  category: string;
  severity?: string;
  reactions?: string[];
  notes?: string;
}

export interface CreateHealthSafetyResponse {
  safety: HealthSafety;
}

/* ── 07 기억 · 교정 ──────────────────────────────────────────────────── */

/**
 * 🚨 **관찰과 프로필은 다른 엔드포인트다** (계약서 §08 "07 화면은 2계층이다").
 *    화면에서도 한 목록에 섞지 않는다 — 관찰 1건과 confirmed 프로필이 같은 줄로 서면
 *    "한 번의 관찰을 성향으로 확정하지 않는다"(CLAUDE.md §2)가 화면에서 사라진다.
 */

/** `GET /children/{cid}/observations`. `Page<T>` 와 달리 `total` 이 함께 온다. */
export interface ObservationsResponse {
  items: Observation[];
  next_cursor: string | null;
  /** 필터를 걸기 전 전체가 아니라 **그 필터의** 건수다. 화면에 그대로 보여준다. */
  total: number;
}

/** 관찰 목록 필터. `?domain=` 생략 시 4개 테이블을 observed_to DESC 로 병합한다. */
export interface ObservationFilters {
  domain?: Agent;
  /** "제안에서 빠진 기억" — 근거로 쓰인 적 없는 것만. */
  unused_in_suggestions?: boolean;
}

/**
 * `GET /children/{cid}/observations/{kind}/{id}` — 관찰 상세.
 * 경로에 `kind` 가 들어가는 이유는 테이블이 4개이기 때문이다.
 */
export interface ObservationDetailResponse {
  observation: Observation;
  /** 🚨 빈 배열이면 "이 기억은 제안 근거에서 빠져 있어요" 를 그린다. */
  used_in: Array<{ suggestion_id: string; content: string; status: SuggestionStatus }>;
  corrections: Array<{ id: string; verdict: CorrectionVerdict; created_at: string }>;
}

/**
 * `GET /children/{cid}/affinities` — 관심·선호는 **한 배열**로 내려온다.
 * 좋아함/싫어함은 별도 배열이 아니라 `polarity` 로 구분한다.
 */
export interface AffinitiesResponse {
  /** 기본 `state != 'archived'`. */
  affinities: Affinity[];
  /** 지워지지 않은 전부. 🚨 감쇠가 없다 — 보호자만 지울 수 있다. */
  safety: HealthSafety[];
}

/**
 * `POST /corrections` — 관찰이든 프로필이든 같은 엔드포인트.
 * 🚨 `target_ref` 는 **객체 1개**다. 배열로 보내면 422.
 */
export interface CorrectionRequest {
  target_ref: Ref;
  verdict: CorrectionVerdict;
  child_id: string;
}

/**
 * 🚨 `cascade` 를 화면이 추측하지 않는다. 무엇이 다시 계산됐는지는 서버만 안다 —
 *    "이 기록을 쓴 추천 1건이 다시 계산됐어요" 는 이 값으로 만든 문구다.
 */
export interface CorrectionResponse {
  correction: { id: string; verdict: CorrectionVerdict; created_at: string };
  /** 갱신 후 상태. 관찰을 고쳤으면 Observation, 프로필을 고쳤으면 Affinity 다. */
  target: Observation | Affinity;
  cascade: {
    affinities_recomputed: Ref[];
    suggestions_recalculated: string[];
  };
}

/* ── 07 제안 피드백 ──────────────────────────────────────────────────── */

/** `PATCH /suggestions/{sid}/feedback`. 🚨 교정(`CorrectionVerdict`)과 다른 축이다. */
export const SUGGESTION_FEEDBACKS = ["child_liked", "child_disliked", "not_acted"] as const;
export type SuggestionFeedback = (typeof SUGGESTION_FEEDBACKS)[number];

/**
 * 🚨 `memory_changed` 는 **항상 false** 다 (계약서 §08). 피드백은 제안 가중치만 바꾸고
 *    기억은 그대로 둔다 — 화면 문구가 이 사실과 같은 말을 해야 한다.
 *    기억을 고치려면 `POST /corrections` 로 간다.
 */
export interface SuggestionFeedbackResponse {
  suggestion: Suggestion;
  memory_changed: boolean;
}

/**
 * ⚠️ **계약서 v1 에 아직 없다.** 계약서 §03 은 07 화면에 `PATCH /feedback` 을 배정했는데
 *    **평가할 제안을 목록으로 얻을 길이 없다.** 프론트가 제안을 지어낼 수는 없으므로
 *    (근거를 달고 나가는 추천이 이 제품의 본체다) 서버가 내려주는 형태로 여기 제안해 두고
 *    목으로 먼저 세웠다. 👉 `apps/api` Owner 협의 대상 (최상위 CLAUDE.md §8).
 *    서버가 안 보내면 피드백 탭은 "아직 받은 제안이 없어요" 를 그린다.
 */
export interface SuggestionListResponse {
  items: Suggestion[];
  next_cursor: string | null;
}

/* ── 08 사진으로 적기 ────────────────────────────────────────────────── */

/**
 * 사진 한 장을 어느 쪽으로 읽을지 (계약서 §09).
 *
 * 🚨 **도메인(`Agent`)이 아니다.** `document` 는 기관이 써 준 글자를 읽는 것이고
 *    `activity` 는 아이가 무엇을 했는지 태그만 뽑는 것이라, 뒤따르는 규칙이 통째로 다르다.
 *    도메인 색을 여기 쓰지 않는 이유이기도 하다.
 */
export const PHOTO_LANES = ["document", "activity"] as const;
export type PhotoLane = (typeof PHOTO_LANES)[number];

/**
 * `POST /photo-runs/{rid}/reanalyze` — 힌트 한 줄을 참고로 **다시 읽는다**. 새 `run_id` 가 온다.
 * 🚨 힌트는 재분석 참고용이고 **그대로 저장되지 않는다** (계약서 §09). 화면 문구가 그 사실을 말한다.
 */
export interface PhotoReanalyzeRequest {
  hint_text: string;
}

export interface PhotoReanalyzeResponse {
  run_id: string;
}

/**
 * `POST /photo-runs/{rid}/commit` — 🚨 **사진 흐름에서 저장이 일어나는 유일한 지점.**
 * 여기 오기 전까지는 아무것도 저장되지 않는다.
 *
 * 🚨 **승인 게이트가 아니다.** 문서 lane 이 만드는 `event` 는 `draft` 고, 캘린더에 쓰는
 *    되돌릴 수 없는 지점은 여전히 `POST /events/{eid}/confirm` 하나다 (CLAUDE.md §2 —
 *    승인 게이트를 늘리지도 줄이지도 않는다). 그래서 이 화면은 `btn-approve` 도 `caution` 도 쓰지 않는다.
 *
 * ⚠️ 계약서 §01 의 Idempotency 목록에 이 엔드포인트가 없다. 관찰을 만드는 쓰기라 두 번 보내면
 *    두 번 쌓일 수 있다 — 화면은 보내는 동안 버튼을 잠그지만 네트워크 재시도까지 막지는 못한다.
 *    👉 `apps/api` Owner 협의 대상 (최상위 CLAUDE.md §8).
 */
export interface PhotoCommitRequest {
  lane: PhotoLane;
  /** 🚨 **고른 것만** 간다. 화면에서 고르지 않은 항목은 저장되지 않는다. */
  selected_items: string[];
  /**
   * 문서 lane 이면 "읽어낸 일시로 일정 초안까지 만들까", 활동 lane 이면 "사진을 그날 캘린더에
   * 함께 남길까" 다 — 한 필드가 lane 에 따라 다른 뜻을 나른다 (계약서 §09 부수 효과 표).
   */
  attach_to_calendar: boolean;
}

/**
 * 🚨 문서에서 읽은 것은 `confidence_source: "institution_notice"` 로 고정된다.
 *    기관 공지가 보호자 발화로 들어가면 출처 추적이 끊긴다 (계약서 §09).
 * 🚨 활동 lane 의 태그는 `affinity_id` 를 달지 않는다 — 사진 한 장을 성향으로 확정하지 않는다.
 */
export interface PhotoCommitResponse {
  observations: Observation[];
  /** 문서 lane 에서 일시를 읽어냈을 때만 온다. `status` 는 `draft` 다. */
  event: CalendarEvent | null;
  /** YYYY-MM-DD. 저장 뒤 "캘린더에서 보기" 가 여는 날짜다 — 프론트가 계산하지 않는다. */
  calendar_date: string | null;
}

/* ── 09 캘린더 ───────────────────────────────────────────────────────── */

/** `GET /children/{cid}/calendar?month=YYYY-MM` 의 한 칸. */
export interface CalendarDay {
  /** YYYY-MM-DD. */
  date: string;
  has_diary: boolean;
  /** 🚨 `confirmed` 만 센다. draft 는 아직 캘린더에 쓴 것이 아니다 (승인 게이트 ㉠). */
  has_event: boolean;
  has_image: boolean;
  observation_count: number;
  /**
   * ⚠️ **계약서 v1 에 아직 없다.** "이날 프로필이 달라졌다"(승격·강등)를 날짜 칸에 그리려면
   *    이 값이 필요한데 월 조회 응답에는 없다. 프론트가 셀 수 없는 값이다 — 승격은 Curator 의
   *    반복 집계 결과라 관찰 건수로 역산할 수 없다 (CLAUDE.md §2).
   *    👉 `apps/api` Owner 협의 대상. 안 오면 고리 표식과 범례의 그 줄이 나오지 않는다.
   */
  profile_changed_count?: number;
}

export interface CalendarMonthResponse {
  days: CalendarDay[];
}

/**
 * `GET /children/{cid}/calendar/{date}`.
 *
 * `events` 는 그 날짜에 걸린 `episodic` 과 `core` 를 함께 담는다 — 화면은 둘을 구분하지 않는다.
 *
 * 🚨 **일기는 관찰로 자동 추출되지 않는다** (계약서 §09). 기억으로 남기려면 보호자가
 *    `POST /children/{cid}/inputs` 에 명시적으로 태워야 한다. 화면이 이 사실을 말한다 —
 *    사용자가 일기라고 쓴 것을 아이 성향으로 조용히 승격시키면 신뢰가 깨진다.
 */
export interface CalendarDayResponse {
  diary: { text: string; image_urls: string[] } | null;
  events: CalendarEvent[];
  observations: Observation[];
}

/** `PUT /children/{cid}/calendar/{date}`. 이미지 업로드는 08 사진 화면 것이다. */
export interface CalendarDayUpdate {
  text: string;
  image_urls: string[];
  event_ids: string[];
}

/** `PATCH /event-items/{iid}` — 준비물 체크. 되돌릴 수 있어서 낙관적 업데이트를 써도 된다. */
export interface EventItemUpdateResponse {
  item: EventItem;
}

export interface Me {
  id: string;
  nickname: string | null;
  children: Array<{
    child_id: string;
    nickname: string;
    /** 나이는 저장하지 않는다. 서버가 birth_date 로 계산해 내린다. */
    age_display: string;
    relation: string;
    role: "owner" | "member";
    consent_required: string[];
  }>;
}

/** ?cursor=&limit= — 커서는 (observed_to, kind, id) 를 base64 로 인코딩한 것. */
export interface Page<T> {
  items: T[];
  next_cursor: string | null;
}

/* ── 00 로그인 ────────────────────────────────────────────────────────── */

/**
 * 로그인 계약의 정본은 docs/api/auth-kakao-v1.md 이고,
 * 프론트가 어떻게 처리하는지는 docs/web/kakao-login-v1.md 에 있다.
 *
 * 🚨 계약서 §04 의 `{access_token}` 은 쓰지 않는다. 클라이언트가 카카오 토큰을 받는
 *    경로가 없어서(카카오 JS SDK 에 그 메서드가 없다) 서버가 인가 코드를 교환하고,
 *    프론트는 서버가 발급한 1회용 코드와 bind 비밀 한 쌍만 보낸다.
 */
export const AUTH_PROVIDERS = ["kakao", "apple", "google", "naver"] as const;
export type AuthProvider = (typeof AUTH_PROVIDERS)[number];

/** GET /auth/{provider}/status — 00 화면 진입 시 prefetch 한다. */
export interface AuthStatus {
  /** 서버 설정이 갖춰졌는가. false 면 버튼을 비활성화한다 — 죽은 버튼을 만들지 않는다. */
  ready: boolean;
  /**
   * 시작 엔드포인트의 **절대 URL**.
   * 🚨 프론트에서 조립하지 않는다 — API_BASE_URL 과 카카오 콜백 오리진이 어긋난 배포에서
   *    state 쿠키가 조용히 깨진다. 서버가 등록된 콜백 URL 에서 파생해 내려준다.
   */
  start_url: string;
}

/** 로그인 시작 쿼리의 `client` — URL 이 아니라 열거값이다 (오픈 리다이렉트 방지). */
export type AuthClient = "web" | "app";

/** POST /auth/{provider} · /signup 의 성공 응답. */
export interface AuthSession {
  /** 불투명 난수 43자. JWT 가 아니다 — 디코딩해서 정보를 꺼내려 하지 않는다. */
  token: string;
  /** 초 (12시간 = 43200). 없으면 401 을 맞고 나서야 만료를 안다. */
  expires_in: number;
  is_new: boolean;
  parent: { id: string; nickname: string | null };
  consent_required: string[];
}

/** 신규 회원 — 동의 전에는 parent 가 없어서 토큰이 없다. `consent_code` TTL 10분. */
export interface AuthSignupPending {
  status: "consent_required";
  consent_code: string;
}

export type AuthExchangeResponse = AuthSession | AuthSignupPending;

/**
 * 🚨 `status` 필드 유무로 분기한다. `token` 유무로 보지 않는다 —
 *    신규 응답에는 token 이 아예 없고, 그 상태로 signIn(undefined) 을 부르면
 *    토큰 없는 세션이 저장돼 이후 모든 요청이 401 이 된다.
 */
export function isSignupPending(res: AuthExchangeResponse): res is AuthSignupPending {
  return "status" in res;
}

/** 계정 스코프 2건. 아이 스코프(child_basic · child_health)는 POST /consents 그대로다. */
export const ACCOUNT_CONSENT_SCOPES = ["service_terms", "privacy_account"] as const;

/** POST /auth/{provider}/signup — 필수 스코프가 빠지면 403 consent_required. */
export interface AuthSignupRequest {
  consent_code: string;
  /** ② 에서 만든 것과 같은 값. consent_code 만으로 계정이 만들어지는 것을 막는다. */
  bind: string;
  consents: Array<{ scope: string; policy_version: string }>;
}

/* ── 01 첫 진입 ──────────────────────────────────────────────────────── */

/** 수집은 별명 · 생일 · 관계까지 (F-13). 프로필 질문을 늘리지 않는다 (CLAUDE.md §2). */
export type Relation = "mother" | "father" | "grandparent" | "sitter" | "other";

export interface CreateChildRequest {
  nickname: string;
  /** YYYY-MM-DD. 나이가 아니라 생일을 받는다 — 나이는 서버가 계산한다. */
  birth_date: string;
  relation?: Relation;
}

export interface CreateChildResponse {
  id: string;
  nickname: string;
  /** 서버가 만든 문구. 프론트에서 다시 계산하지 않는다. */
  age_display: string;
  role: "owner" | "member";
}

/* ── 02 이야기 하나 ──────────────────────────────────────────────────── */

/** 🚨 발달 검사가 아니다. 보호자가 고른 값만 저장하고 AI 는 평가하지 않는다. */
export interface DevScreeningItem {
  item_id: string;
  text: string;
  levels: Array<{ level: number; label: string }>;
}

export interface DevScreeningResponse {
  age_band: string;
  /** 나이대 밖이면 빈 배열이다. */
  items: DevScreeningItem[];
}

/**
 * 🚨 "잘 모르겠어요"(`unknown`) 는 "없음"(`none`) 이 아니다.
 *    unknown 이면 서버가 health_safety 에 아무것도 쓰지 않고, 이후 Food Agent 는
 *    guards.safety_unknown 으로 **실행 자체가 막힌다** (CLAUDE.md §2 — 조회 실패 시 실행 금지).
 */
export type SafetyStatus = "none" | "has" | "unknown";

/** 🚨 보호자가 직접 입력한 값만 들어간다. LLM 이 추론한 알레르기는 저장하지 않는다 (NF-03). */
export interface OnboardingSafetyInput {
  type: string;
  label: string;
  category: string;
  severity?: string;
  reactions?: string[];
}

/** 전부 선택이다. 모두 건너뛰어도 200 이다. */
export interface OnboardingRequest {
  interests?: string[];
  safety_status?: SafetyStatus;
  safety?: OnboardingSafetyInput[];
  /**
   * ⚠️ 화면에서 보내지 않는다. 온보딩에서 한 줄을 또 받으면 03 홈의 입력과 같은 것을
   * 두 번 묻는 셈이라 뺐다 — 계약서에는 남아 있어서 타입만 유지한다.
   */
  one_line?: string;
  dev_answers?: Array<{ item_id: string; level: number }>;
}

export interface OnboardingResponse {
  observations: Observation[];
  affinities: Affinity[];
  safety: HealthSafety[];
  /** 서버가 저장하지 않고 돌려보낸 항목. safety_status: "unknown" 이 여기 담긴다. */
  skipped: string[];
  run_id: string;
}

/* ── 동의 ─────────────────────────────────────────────────────────────── */

/**
 * POST /consents (계약서 §04). append-only — 철회도 `withdrawn` 행을 **추가**한다.
 *
 * 🚨 계정 스코프면 `child_id` 를 생략한다. 아이 스코프인데도 아이가 아직 없는 경우가
 *    가입 직후다 — `child_basic` 없이 `POST /children` 이 403 이라, 아이를 만들기 전에
 *    받아야 하기 때문이다 (#18 확인 대상).
 */
export interface ConsentRequest {
  scope: string;
  action: "granted" | "withdrawn";
  child_id?: string;
  policy_version: string;
  /** 아이 스코프에서 보호자임을 확인한 표시. */
  guardian_attested?: boolean;
}

export interface ConsentResponse {
  consent: { id: string; scope: string; action: string; acted_at: string };
  effective: Record<string, boolean>;
}
