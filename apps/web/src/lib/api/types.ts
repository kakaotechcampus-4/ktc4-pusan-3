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
  /**
   * ⚠️ **계약서 v1 의 목록에 없다.** `docs/agents/data_model.md` 의 `suggestion_evidence`
   *    (`source_kind`) 가 급식 행을 근거로 든다고 적어 뒀는데(2026-09-23 회의 확정) `Ref` 에
   *    그 종류가 없어서, 그런 응답이 타입에 안 맞는다.
   *
   * ⚠️ **어긋나는 것이 이것만이 아니다.** 같은 문서의 `source_kind` 목록에는 `Ref` 에 없는
   *    종류가 더 있다 — `observation_routine` · `child_growth_log` · `notice` · `intake_daily` ·
   *    `food_doc` · `growth_doc` · `activity_doc`. 반대로 `Ref` 의 `health_safety` · `event` ·
   *    `suggestion` 은 근거로 쓰이지 않는다(`Ref` 는 교정 대상 등 다른 자리에도 쓰인다).
   *    **근거의 종류를 `Ref` 와 같은 enum 으로 둘 것인지부터** 정해야 한다.
   *    👉 `apps/api` Owner 협의 대상 (#151).
   */
  "daycare_meal",
] as const;
export type RefKind = (typeof REF_KINDS)[number];

export interface Ref {
  kind: RefKind;
  id: string;
}

/* ── 도메인 enum ──────────────────────────────────────────────────────── */

/**
 * 🚨 코드에서는 play/interest 가 아니라 activity 로 통일한다 (CLAUDE.md §5).
 * 🚨 `growth` 는 관찰 테이블 이름과 다르다 — 이 Agent 가 `observation_education` 과
 *    `observation_routine` 둘을 읽는다. 접미사로 서로를 유추하지 말 것.
 */
export const AGENTS = ["food", "activity", "growth", "health"] as const;
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
  /**
   * 그 근거 행에서 **추천 근거로 채택한 내용** ("계란말이 요청 · 지은 적음 · 반복 5회").
   * Agent 가 쓰고 **보호자 화면에 그대로 나간다** (`docs/agents/data_model.md` ·
   * `suggestion_evidence.note` · 2026-09-25 확정).
   *
   * 🚨 **필수다.** 그 문서가 `NOT NULL` 이고 "비면 그 후보를 거절한다" 이므로, `note` 없는
   *    근거는 화면에 도달할 수 없다. optional 로 두고 화면이 대신 문장을 만들면 **거절됐어야
   *    할 후보를 UI 가 덮는다** (근거 0건을 그리지 않는 것과 같은 규칙 · CLAUDE.md §2).
   *
   * 🚨 **왜 조립하지 않고 문구로 받나** — 근거는 종류가 여럿이다. 성향(`profile_affinity`)이면
   *    반복 횟수가 뜻이 있고, 급식 행(`daycare_meal`)이면 "어린이집에서 받음 · 기관 기록" 이고,
   *    알레르기 규칙이면 "규칙 확인 · 보호자 입력값" 이다. 화면이 종류마다 다른 문장을 조립하려면
   *    **근거의 의미를 프론트가 알아야** 하고, 그러면 반복 횟수 같은 판단이 화면으로 샌다
   *    (CLAUDE.md §3 — 100% 맞아야 하는 것은 코드가, 그것도 서버가 한다).
   *    `Observation.observed_label` · `Affinity.state_reason` 과 같은 성격이다.
   *
   * ⚠️ **계약서 v1(§02 `Evidence`)에는 아직 이 필드가 없다.** DB 문서에만 있다 —
   *    응답에 실어 주는 것까지 확인이 필요하다. 👉 `apps/api` Owner 협의 대상 (#151).
   */
  note: string;
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
/**
 * ⚠️ **화면이 실제로 쓰는 것은 `operations.ts` 의 `InputRequest` 다.** 계약서 §04 를 옮긴 이
 * 타입과 두 벌로 갈려 있고 `source` 의 열거도 다르다 — 합치는 것은 별도 정리 대상이다.
 * 새 필드는 **양쪽에 같이** 넣는다.
 */
export interface CreateInputRequest {
  text: string;
  source: "home_input" | "photo" | (string & {});
  /** 🔶 되묻기에 대한 답일 때만. 그 질문이 나온 `run_id` (#158 리뷰 · 서버 #175). 못 찾으면 400. */
  reply_to?: string;
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
 * 한 Agent 가 낸 후보 묶음의 **머리말**. 🚨 후보 자체는 `suggestions` 에 평평하게 담기고,
 * 화면이 `agent` 로 묶는다 — 여기 담기는 것은 그 묶음에 대해 **서버만 아는 것**뿐이다.
 *
 * 🚨 **왜 묶음이 필요한가** — 한 Agent 가 후보를 **3가지씩** 낸다 (저녁 제안으로 김치찌개 ·
 *    계란말이 · 멸치볶음). 그 셋은 서로 다른 제안이 아니라 **한 결정에 대한 대안**이다.
 *    평평한 목록으로 그리면 보호자는 여섯 개의 독립 제안으로 읽고, "무엇을 정하는 중인가" 가 사라진다.
 *
 * ⚠️ **계약서 v1 에 아직 없다.** 👉 `apps/api` Owner 협의 대상 (최상위 CLAUDE.md §8 · #151).
 *    서버가 안 보내면 화면은 도메인 라벨로만 머리줄을 만들고, 묶임은 **말하지 않는다**.
 */
export interface SuggestionGroup {
  agent: Agent;
  /**
   * 이 묶음이 무엇을 정하는 중인가 ("오늘 저녁 뭐 먹을까요"). 🚨 **서버 문구다** —
   * 무엇을 묻는 중인지는 후보를 만든 쪽만 안다. 프론트가 지어내지 않는다.
   * (03 홈의 `agent_prompts` · 04 의 `offer.label` 과 같은 성격이다.)
   */
  prompt?: string;
  /**
   * 🚨 **이 묶음에서 고른 것들이 일정 하나로 묶이는가.** `food` 가 그렇다 — 반찬 셋을 골라도
   *    "저녁 식사" 한 건이 된다. 묶는 판단은 도메인 지식이라 서버가 하고, 화면은 **미리 말하기만** 한다.
   *    말하지 않으면 보호자는 3건을 고르고 일정 3건을 기대하는데 1건이 나온다.
   */
  merges_into_one?: boolean;
}

/**
 * 🚨 `suggestions` 가 비어 있는데 `scarcity` 도 null 인 응답은 없다.
 *    둘 다 비면 화면에 그릴 것이 없다는 뜻이고, 그건 서버 버그다.
 */
export interface SuggestionsResponse {
  /**
   * 🚨 개인화만 담는다. 일반 추천은 아래 `general` 이다 — 한 배열에 섞지 않는다.
   * 🚨 **한 Agent 가 3가지씩 낸다.** Agent 는 최대 2개라(NF-01) 여기 최대 6건이 담긴다.
   */
  suggestions: Suggestion[];
  /**
   * 묶음 머리말. ⚠️ 계약서에 아직 없다 (`SuggestionGroup` 참고).
   * 🚨 후보를 담지 않는다 — 묶는 것은 화면이 `agent` 로 한다.
   */
  groups?: SuggestionGroup[];
  /**
   * 또래 기준 일반 추천. `scarcity` 가 있을 때만 채워진다 (개인화 **대신** 나가는 것이라
   * 둘이 같이 나오지 않는다). 위 ⚠️ 참고 — 계약서에 아직 없어서 optional 이다.
   */
  general?: GeneralSuggestion[];
  /** "오늘 급식 · 최근 3일 식사 · 확정 관심 2건" — 무엇을 봤는지. 서버가 만든 문구다. */
  looked_at: string;
  guards: Guard[];
  scarcity: Scarcity | null;
  /**
   * 알레르기 사전검사. 🚨 **고르는 화면이 미리 들고 있어야 한다** — 채택 버튼을 누른 그 자리에서
   *    물어야 하고, 그러려면 물어볼 것이 화면에 이미 있어야 한다 (`Precheck` 머리말 · #151).
   *
   * ⚠️ **계약서에 아직 없다.** 계약서는 이 검사를 초안을 만드는 응답에 실어 뒀는데, 그 자리가
   *    "일정을 만들기로 한 뒤" 라서 **일정을 안 만들면 영영 안 묻는다.**
   *    👉 `apps/api` Owner 협의 대상 (최상위 CLAUDE.md §8 · #151).
   *    서버가 안 보내면 빈 배열이고, 화면은 묻지 않는다 — 🚨 **그때는 "확인했다" 고 말하지도 않는다.**
   */
  prechecks?: Precheck[];
}

/** 되묻는 질문의 답. 관찰 1건으로 저장된다 (confidence_source: parent_direct). */
export interface AnswerRequest {
  question_id: string;
  answer: string;
}

/* ── 일정 초안 (event draft) ─────────────────────────────────────────── */

/**
 * 🚨 **초안이 만들어지는 경로는 셋인데 payload 는 한 벌이다** (#121 · #122 합의).
 *
 *   ㉠ 한 줄 입력   "금요일에 물놀이 있어"        → Memory Agent  → SSE `event_draft`
 *   ㉡ 제안 카드    activity 제안 "나들이"        → suggestion 도메인 (서버)
 *   ㉢ 사진(알림장) 한 장에 일정 여러 건          → OCR 파이프라인
 *
 * 모양이 갈리면 화면이 여러 벌이 된다. 그래서 세 경로가 같은 JSON 을 내고 화면은
 * `EventDraftCard` 한 벌로 그린다. 정본은 `apps/api/app/agents/memory/drafts.py` 의
 * `EventDraft.to_payload()` 이고, 문서는 [`docs/event/event-draft-flow-v1.md`] 다.
 *
 * 🚨 **초안은 DB 에 없다.** 9/17 에 `event.status` 가 없어지면서 승인 전 행을 두지 않기로 했다 —
 *    `event` 테이블에는 보호자가 제출한 행만 들어간다. 초안은 SSE 와 응답에만 존재한다.
 * 🚨 **제출이 승인 게이트 ㉠ 이다** (`POST /children/{cid}/events`). 초안을 만드는 호출은
 *    아무것도 쓰지 않는다 — `POST /suggestions/{sid}/event` 도 #121 에서 쓰기를 뗐다.
 * 🚨 **제출은 건별이다** (9/21 회의). 초안 여러 장을 한 요청으로 묶지 않는다.
 */

/** 초안 안의 일정 본체. `CalendarEvent` 와 달리 `id` · `status` · `reminders` 가 없다 — 아직 행이 아니다. */
export interface EventDraftFields {
  title: string;
  /**
   * ⚠️ **`null` 이 될 수 있는 것이 `CalendarEvent` 와 다른 점이다.** 제안에서 온 초안은
   *    제안 문장만으로 일자를 알 수 없어서 비워서 내려온다 — 🚨 **프론트가 오늘로 채우지 않는다**
   *    (`PhotoEntry.date` 와 같은 규칙). 보호자가 채우기 전에는 제출할 수 없다.
   */
  starts_at: string | null;
  ends_at: string | null;
  all_day: boolean;
  event_type: CalendarEvent["event_type"];
  category: CalendarEvent["category"];
}

/**
 * 초안의 준비물 한 줄.
 *
 * 🚨 **`is_prepared` 가 없다** (#122 확정). 체크 상태는 `PATCH /event-items/{iid}` 만의 몫이고,
 *    초안이 정하는 것은 새 준비물(`item_id: null`)의 초기값뿐이다. payload 에 실으면
 *    SSE 가 나간 뒤 보호자가 체크한 것이 제출하는 순간 풀린다.
 */
export interface EventDraftItem {
  /** 🚨 `null` = 아직 저장 전인 새 준비물. 값이 있으면 기존 행이다. */
  item_id: string | null;
  item_name: string;
}

/** 수정 초안의 **원본 전체**. 화면이 "오후 3시 → 오후 5시" 를 그리는 데 쓴다. */
export interface EventDraftBefore extends EventDraftFields {
  items: EventDraftItem[];
}

export interface EventDraft {
  /**
   * 🚨 **화면 전용 키다** (#122 재리뷰). 서버는 이 값을 읽지 않고, 제출 요청에도 싣지 않는다.
   *    화면이 카드를 가리키고 세션 스토리지 키로 쓴다 — 배열 인덱스로는 한 장 제출 뒤 나머지가 밀린다.
   */
  draft_id: string;
  /** 화면이 부를 엔드포인트를 이 값으로 고른다. */
  op: "create" | "update";
  /** 🚨 `create` 는 언제나 `null` 이다. */
  event_id: string | null;
  event: EventDraftFields;
  /** 🚨 `create` 는 `null`. `update` 면 준비물까지 포함한 원본 전체다. */
  before: EventDraftBefore | null;
  /**
   * 🚨 **제출 시점의 최종 목록이다.** 항목별 op 가 없어서, 배열에서 빠진 `item_id` 가
   *    삭제를 표현하는 유일한 방법이다 (#122 확정).
   */
  items: EventDraftItem[];
  /**
   * ⚠️ **계약서에 아직 없는 필드다.** 값을 못 읽었거나 서버가 자신이 없을 때 참이다 —
   *    사진 경로의 `PhotoEntry.needs_review` 와 **같은 모양으로 맞추자는 제안**이다.
   *    세 경로가 빈 필드를 저마다 다른 방식으로 말하면 카드가 경로별로 분기한다.
   *    👉 `apps/api` Owner 협의 대상 (최상위 CLAUDE.md §8).
   */
  needs_review?: boolean;
  /** 왜 확인이 필요한지. 🚨 서버 문구다 — 프론트가 지어내지 않는다. */
  review_reason?: string;
  /**
   * ⚠️ 이 초안이 어느 제안(들)에서 왔는지. 제안 경로에만 있다 (#122 에서 모양이 미정으로 남은 자리).
   *    제출받는 쪽이 **제안 ↔ 일정 연결**을 남기려고 필요하다 (#206). 🚨 상태를 바꾸려는 것이 아니다 —
   *    초안이 되는 제안은 이미 채택(`approved`)된 것이다.
   *
   * 🚨 **배열인 이유** — `food` 제안 여러 건이 한 끼로 묶여 초안 하나가 된다. 연결이 N:1 이라
   *    단수로 두면 묶인 나머지 제안은 일정이 됐다는 사실이 남지 않는다.
   */
  suggestion_ids?: string[];
}

/**
 * 초안 제출 본문 — 🚨 **승인 게이트 ㉠. 되돌릴 수 없는 지점.**
 *
 * 🚨 **`op` 에 따라 엔드포인트가 갈린다** (9/21 회의): create 는 `POST`, update 는 `PATCH`.
 *    그래서 본문에 `op` 도 `event_id` 도 싣지 않는다 — 어느 일정인지는 경로가 말한다.
 * ⚠️ **경로와 요청 스키마는 아직 미정이다** ([`docs/event/event-draft-flow-v1.md`] §6).
 *    아래는 확정된 payload 계약(#122)에서 **화면이 되돌려 보낼 수 있는 부분만** 추린 잠정형이다.
 *
 * 🚨 **보호자가 확인한 최종 상태를 통째로 보낸다** (#122 확정). 부분 갱신이 아니라서
 *    `items` 배열에서 빠진 `item_id` 가 삭제로 처리된다.
 * 🚨 `draft_id` 를 싣지 않는다 (§4-4 — 화면 전용 키고 서버는 읽지 않는다).
 * 🚨 `items` 에 `is_prepared` 가 없다 (§4-3 — 체크는 `PATCH /event-items/{iid}` 만의 몫).
 */
export interface SubmitEventBody {
  event: EventDraftFields;
  items: EventDraftItem[];
  /**
   * 제출받는 쪽이 제안 ↔ 일정 연결을 남기려고 필요하다 (#206). 🚨 제안의 `status` 는 바뀌지 않는다.
   * 제안 경로(create)에만 있다. 🚨 **배열이다** — `food` 제안 여러 건이 한 초안으로 묶인다.
   * ⚠️ 키 이름과 모양이 미정이다 (§6 · #122 에서 "제안 경로 PR 에서 정한다" 로 남은 자리).
   */
  suggestion_ids?: string[];
}

/* ── 06 승인 ─────────────────────────────────────────────────────────── */

/**
 * 고른 제안을 **채택한다** — `suggestion.status` 를 `approved` 로 바꾼다.
 *
 * 🚨 **일정과 다른 축이다.** 이걸 부른다고 캘린더에 아무것도 안 들어간다. 부모가 "이걸로 할게요"
 *    라고 말한 것을 남기는 자리이고, 일정으로 만들지는 **그다음에** 따로 묻는다.
 *
 * 🚨 **`approved` 의 뜻은 "보호자가 채택했다" 하나다** (#196 멘토 리뷰 · #206). 새 상태(`chosen`)를
 *    두지 않는다. 일정이 됐는지는 상태가 아니라 **제안 ↔ 일정 연결**로 본다 — 그래서 일정을 제출해도
 *    `status` 는 그대로고, 일정이 나중에 지워져도 채택은 남는다. 실제로 했는지는 `feedback` 이 따로 받는다.
 *    24시간 만료는 **`draft` 에만** 걸린다 (고르지 않은 제안).
 *
 * ⚠️ **계약서에 없는 엔드포인트다.** 👉 `apps/api` Owner 협의 대상 (최상위 CLAUDE.md §8 · #151).
 *
 * 🚨 **승인 게이트가 아니다.** 되돌릴 수 없는 2곳은 캘린더 쓰기와 건강·알레르기 확정뿐이라
 *    (최상위 §2), 이 버튼에 `btn-approve` 도 `caution` 도 쓰지 않는다.
 * 🚨 **Idempotency-Key 를 쓰지 않는다.** 같은 id 를 두 번 보내도 결과가 같은 상태 전환이고,
 *    되돌릴 수 없는 5곳에 여섯 번째를 더하지 않는다 (`lib/api/idempotency.ts`).
 */
export interface ApproveSuggestionsRequest {
  suggestion_ids: string[];
}

/** 🚨 **바뀐 제안을 그대로 돌려받는다** — 화면이 `status` 를 지어내지 않는다. */
export interface ApproveSuggestionsResponse {
  suggestions: Suggestion[];
}

/**
 * 채택한 제안들을 일정 초안으로 바꾼다. 승인 게이트 ㉠ **준비**다 — 여기서는 아무것도 쓰지 않는다.
 *
 * ⚠️ **계약서에 없는 엔드포인트다.** 05 에서 제안을 **여러 개 고를 수 있게** 되면서 생겼다
 *    (기존 `POST /suggestions/{sid}/event` 는 한 건짜리였다).
 *    👉 `apps/api` Owner 협의 대상 (최상위 CLAUDE.md §8 · #151).
 *
 * 🚨 **몇 장이 나오는지는 서버가 정한다.** 고른 개수와 초안 개수가 1:1 이 아니다 —
 *    **`food` 제안들은 한 끼로 묶여 초안 1건**이 되고(저녁 식사 일정), 나머지 Agent 의 제안은
 *    고른 수만큼 나온다. 묶는 판단은 도메인 지식이라 화면이 하지 않는다.
 *
 * ⚠️ `starts_at` 을 프론트가 만들지 않는다. 날짜를 계산하지 않는다는 규칙(CLAUDE.md §3) 때문에
 *    시각은 서버가 제안에서 정하고, 화면은 표시만 한다. 제안만으로 일자를 모르면 `null` 로 오고
 *    보호자가 카드에서 고른다.
 */
export interface CreateEventDraftsRequest {
  /** 🚨 보호자가 고른 제안. 순서는 화면 순서이고 서버가 묶을 때 쓸 수 있다. */
  suggestion_ids: string[];
}

/**
 * 알레르기 사전검사. 🚨 규칙이 만들고 모델은 관여하지 않는다 (최상위 §3).
 * `unknown_ingredient` = 알레르기 기록에 없는 재료 — 보호자에게 물어야 한다.
 *
 * 🚨 **묻는 자리는 제안을 채택할 때다** (#151). 한동안 초안을 만든 뒤(=일정을 만들기로 한 뒤)
 *    물었는데, 채택과 일정 만들기가 갈리면서 **일정을 안 만들면 영영 안 묻는** 구멍이 생겼다.
 *    알레르기는 캘린더가 아니라 **그 음식을 먹이는 일**에 걸린 위험이다.
 * 🚨 **채택을 되돌리는 길이 없으므로 채택보다 먼저 묻는다.** 알레르기가 확인된 제안은
 *    채택 자체를 하지 않는다 — 그러면 초안도 안 생기고, 막을 것도 남지 않는다.
 */
export interface Precheck {
  code: "unknown_ingredient" | (string & {});
  item: string;
  note: string;
  /**
   * ⚠️ **계약서에 아직 없는 필드다.** 어느 제안 때문에 묻는 것인지 — 여러 개를 고를 수 있게
   *    되면서 필요해졌다. 없으면 화면은 **재료 하나로 고른 것 전부를 막는다**:
   *    식사 제안의 알레르기 때문에 놀이 제안까지 못 고르는 것은 규칙이 아니라 버그지만,
   *    알레르기에서 **덜 막는 쪽으로 기울 수는 없어서** 모르면 전부 막는다.
   *    👉 `apps/api` Owner 협의 대상 (최상위 CLAUDE.md §8 · #151).
   */
  suggestion_id?: string;
}

export interface CreateEventDraftsResponse {
  /**
   * ⚠️ **여기 있던 `prechecks` 를 뺐다** (#151). 알레르기는 **채택할 때** 묻는다
   *    (`SuggestionsResponse.prechecks`) — 일정을 안 만들면 영영 안 묻게 되기 때문이다.
   *    서버가 여기 계속 실어도 화면은 읽지 않는다.
   */
  /**
   * 🚨 **저장된 `event` 가 아니라 초안이다** (#121 확정). `id` · `status` · `expires_at` 이 없다 —
   *    이 호출은 DB 에 쓰지 않으므로 아직 행이 아니다.
   */
  drafts: EventDraft[];
}

/**
 * 제출 응답. 🚨 **승인 게이트 ㉠ 을 지난 뒤**라 여기 오는 `event` 는 실제로 저장된 행이다.
 *
 * 🚨 **제안의 `status` 를 싣지 않는다** (#206). 제출은 제안 상태를 바꾸지 않는다 — 초안이 된
 *    제안은 이미 `approved` 고, 제출이 남기는 것은 제안 ↔ 일정 연결이다.
 */
export interface SubmitEventResponse {
  event: CalendarEvent;
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

/**
 * 🚨 **승인 게이트 ㉡ — 고치기.** ⚠️ 계약서 v1 에 없다 (이슈 #87).
 *
 * 🚨 **`type` 과 `label` 이 없다.** 그 둘은 이 기록의 **정체**다 — 우유를 땅콩으로 고치는 것은
 *    고치기가 아니라 다른 기록이고, `UNIQUE(child_id, type, label)` 과 "이미 등록된 항목" 판정이
 *    같이 흔들린다. 항목이 잘못됐으면 **내리고(`DELETE`) 새로 등록**한다.
 *    고칠 수 있는 것은 그 항목에 대해 **나중에 알게 된 것**뿐이다.
 *
 * 🚨 보호자 직접 입력만 들어온다. LLM 이 이 요청을 만들지 않는다 (NF-03 · 최상위 §2).
 */
export interface UpdateHealthSafetyRequest {
  category?: string;
  /** `null` 은 "모르겠어요" 로 되돌리는 것이다 — 값을 안 보내는 것(그대로 두기)과 다르다. */
  severity?: string | null;
  reactions?: string[];
  notes?: string | null;
}

export interface UpdateHealthSafetyResponse {
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
 * 사진 한 장에서 읽어낸 **항목 하나** (문서 lane).
 *
 * ⚠️ **계약서 v1 에 없는 모양이다.** §09 의 `parsed.extracted` 는 `items` · `when` 하나뿐이라
 *    **항목 한 개**만 담을 수 있는데, 실제로 들어오는 것은 그렇지 않다 — 알림장 한 장에 일정이
 *    여러 개 적혀 있고, 식단표는 거의 **한 달치**가 한 장이다. 배열이 아니면 화면이 첫 개만
 *    보여주거나 전부 한 덩어리로 뭉개야 한다.
 *    👉 `apps/api` Owner 협의 대상 (최상위 CLAUDE.md §8).
 *
 * 🚨 **활동 lane 과 한 배열에 섞지 않는다.** 활동은 날짜도 준비물도 없는 태그라
 *    `ParsedEvent.tags` 로 따로 온다 — 모양이 다른 둘을 한 필드에 넣으면 화면이 플래그로
 *    갈라야 하고, 그러면 저장 경로가 섞인다 (일반 추천과 개인화 추천을 안 섞는 것과 같은 규칙).
 */
export const PHOTO_ENTRY_KINDS = ["event", "supply", "meal"] as const;
export type PhotoEntryKind = (typeof PHOTO_ENTRY_KINDS)[number];

export interface PhotoEntry {
  id: string;
  kind: PhotoEntryKind;
  /** 화면에 그대로 세우는 한 줄. 서버가 만든 문구다. */
  title: string;
  /** `YYYY-MM-DD`. 🚨 **못 읽었으면 `null`** — 프론트가 오늘로 채우지 않는다. */
  date: string | null;
  all_day?: boolean;
  /** 준비물처럼 딸린 항목. 없으면 빈 배열. */
  items: string[];
  /**
   * 🚨 **확인이 필요한가.** 값을 못 읽었거나 서버가 자신이 없을 때 참이다.
   *    화면은 이 값 하나로 위(확인이 필요해요)와 아래(잘 읽었어요)를 가른다.
   */
  needs_review: boolean;
  /** 왜 확인이 필요한지. 서버가 만든 문구 — 프론트가 지어내지 않는다. */
  review_reason?: string;
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
  /**
   * 문서 lane — **보호자가 확인한 항목만** 간다 (고친 값이 실려 있다).
   * 🚨 확인하지 않은 항목(`needs_review` 인 채로 둔 것)은 **빼고 보낸다.** 값을 못 읽은 것을
   *    그대로 저장하면 "승인 전에는 저장되지 않아요" 가 문구만 남는다.
   * ⚠️ 계약서 v1 의 `selected_items`(문자열 배열)를 대신한다 — `PhotoEntry` 의 ⚠️ 참고.
   */
  entries?: PhotoEntry[];
  /** 활동 lane — 보호자가 고른 태그. 🚨 **고른 것만** 간다. */
  selected_tags?: string[];
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
  /**
   * ⚠️ **저장 대상이 계약서와 AI 파트 문서에서 다르다.** 계약서 §09 의 lane 표는 문서 lane 을
   *    `observation_education 또는 event 만` 으로 적어 뒀는데, `docs/agents/data_model.md`
   *    (2026-09-26)는 기관 문서를 **별도 테이블**로 뒀다 — `notice`(OCR 3갈래 분류의 일반 공지 ·
   *    테이블 신설) · `daycare_meal`(INSERT 는 OCR·급식 배치만). 즉 문서 lane 이 만드는 것은
   *    **아이 기록이 아닐 수 있다.**
   *
   * 🚨 **화면은 이 응답을 쓰지 않는다** — 저장하면 바로 홈이라(#151) 지금은 타입만 남아 있다.
   *    응답 모양을 고치기 전에 **무엇이 저장되는지부터** 정해야 한다.
   *    👉 `apps/api` Owner 협의 대상 (최상위 CLAUDE.md §8).
   */
  observations: Observation[];
  /**
   * 🚨 **일정은 여기서 만들어지지 않는다** (#151). 커밋이 만드는 것은 **관찰**이고, 일정은
   *    고치기 시트의 승인 게이트(`POST /children/{cid}/events`)가 그 자리에서 넣는다 —
   *    무엇을 넣는지 아는 자리와 넣는 자리를 갈라 두면 같은 항목을 두 화면에서 두 번 확인하게 된다.
   *
   *    `event.status` 가 없어진 뒤(#118) 이 커밋이 게이트 없이 캘린더에 쓰는 유일한 경로였는데,
   *    일정을 아예 안 만들면 그 경로 자체가 사라진다.
   */

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

/* ── 약관 조회 ────────────────────────────────────────────────────────── */

/**
 * GET /policies — 동의 화면이 그릴 약관 목록 (#91 · #172).
 *
 * 🚨 **무인증이다.** 동의 화면은 계정이 만들어지기 전에 뜬다 (최상위 CLAUDE.md §9 의 예외 목록).
 * 🚨 **배열 순서가 화면 순서다.** 서버가 계정 동의 → 아이 동의 순으로, 민감정보를 마지막에
 *    둔다 (`app/domains/policy/catalog.py`). 프론트에서 다시 정렬하지 않는다.
 * 🚨 **여기 없는 scope 는 화면에 세우지 않는다.** 동의를 받지 않는 scope 가 있고, 그건
 *    응답에 담기지 않는다.
 */
export interface Policy {
  scope: string;
  /**
   * 🚨 동의를 보낼 때 **그대로 싣는 값**이다 (`consents[].policy_version`). 프론트가 상수로
   *    들고 있지 않는다 — 서버가 등록해 둔 버전과 다르면 `400 policy_version_invalid` 다.
   */
  version: string;
  /** 법적 표기. 체크박스 라벨이자 전문 페이지의 제목이다. */
  label: string;
  /** 근거 조문. 없으면 보여주지 않는다. */
  legal_basis: string | null;
  /** 🚨 제출을 막는 것은 이 값뿐이다 — 목록 길이로 세지 않는다. */
  required: boolean;
  /** 민감정보라 다른 동의와 구분해서 받아야 하는가 (개인정보보호법 제23조). */
  sensitive: boolean;
  /**
   * 누구의 동의라서 **어느 화면이 묻는가** (#190). `account` 는 가입 화면이 묻고 가입 요청에
   * 싣고, `child` 는 01 아이 만들기 화면이 아이와 한 트랜잭션으로 보낸다.
   *
   * 🚨 **화면이 scope 이름으로 무리를 나누지 않는다.** 서버가 가입 검사에 쓰는 것과 같은
   *    기준(`ACCOUNT_SCOPES`)이라, 새 동의가 생겨도 화면이 추측할 일이 없다 (#189 리뷰).
   */
  target: "account" | "child";
  /**
   * "전문 보기" 가 여는 정본 HTML 의 경로. `API_BASE_URL` 뒤에 그대로 붙인다
   * (`policyHref()` · `lib/consent.ts`).
   *
   * 🚨 **`null` 이면 정본이 아직 없다** (개정 중인 아이 동의 2건). 그때는 전문 보기를
   *    **숨긴다** — 다른 글을 대신 보여주지 않는다. 화면이 약관 본문을 직접 그리지 않는
   *    이유가 "보호자가 본 글" 을 서버가 증명할 수 있어야 해서다 (멘토 #71-4 · #179).
   */
  html_path: string | null;
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
  /**
   * 계정이 여기서 만들어지는데 이름을 받을 다른 엔드포인트가 없다 — 계약서에 `PATCH /me`
   * 가 없어서, 이 바디에 싣지 않으면 보호자 이름을 저장할 길 자체가 없다.
   * 서버가 받기로 했다 (#90 · `SignupRequest.nickname`).
   */
  nickname: string;
  /**
   * 🚨 `policy_version` 은 **`GET /policies` 가 방금 준 값**이다. 상수로 들고 있지 않는다 —
   *    서버는 지금 유효한 그 버전 하나만 받고, 다르면 `400 policy_version_invalid` 로
   *    돌려보낸다 (대기표는 살아 있으니 화면만 다시 불러오면 된다).
   */
  consents: Array<{ scope: string; policy_version: string }>;
}

/* ── 01 첫 진입 ──────────────────────────────────────────────────────── */

/** 수집은 별명 · 생일 · 관계까지 (F-13). 프로필 질문을 늘리지 않는다 (CLAUDE.md §2). */
export type Relation = "mother" | "father" | "grandparent" | "sitter" | "other";

export interface CreateChildRequest {
  nickname: string;
  /** YYYY-MM-DD. 나이가 아니라 생일을 받는다 — 나이는 서버가 계산한다. */
  birth_date: string;
  /**
   * ⚠️ **`relation` 이 여기서 빠졌다** (계약서 §05 에는 아직 있다 · 확정 전).
   *    02 온보딩으로 옮겼다 — 01 은 **되돌리기 어려운 것**(별명·생일·법정대리인 동의)만
   *    받고, 고를 수 있는 것은 전부 다음 화면이 받는다.
   *    🚨 두 곳에서 받지 않는다. 같은 값을 두 엔드포인트가 쓰면 어느 쪽이 정본인지 사라진다.
   */
  /**
   * 아이 스코프 동의 2건 (`child_basic` · `child_health`).
   *
   * ⚠️ **계약서 §05 의 바디에 없다** (#96 에서 추가 요청 중 · 확정 전).
   *
   * 🚨 **아이를 만드는 것과 같은 트랜잭션이어야 한다.** 동의를 아이 단위로 기록하기로
   *    하면서(#96) 닭-달걀이 생겼다 — `POST /consents` 는 `child_id` 를 받는데 그
   *    `child_id` 는 이 호출 전에는 없고, 계약서 §04 는 `child_basic` 없이 이 호출이
   *    403 이라고 말한다. 둘을 동시에 만족시키는 모양은 이것뿐이다.
   */
  consents: Array<{ scope: string; policy_version: string }>;
  /**
   * 법정대리인임을 보호자가 확인한 표시 (개인정보보호법 제22조의2).
   * **만 19세 이상 표시를 겸한다** — 화면 문항이 약관 제6조 ② 문장 그대로 둘을 함께 묻는다 (#166).
   *
   * 🚨 **화면의 체크박스 값을 그대로 싣는다.** 상수 `true` 를 보내지 않는다 — 그러면
   *    아무도 확인하지 않은 동의가 확인된 것으로 남는다.
   * 🚨 초대로 들어온 보호자는 이 값을 보낼 일이 없다. 법정대리인 동의는 아이당 한 번,
   *    아이를 등록하는 보호자가 한다 (#96).
   */
  guardian_attested: boolean;
}

export interface CreateChildResponse {
  id: string;
  nickname: string;
  /** 서버가 만든 문구. 프론트에서 다시 계산하지 않는다. */
  age_display: string;
  role: "owner" | "member";
}

/* ── 11 알레르기 검사지 읽기 ─────────────────────────────────────────── */

/**
 * ⚠️ **계약서 v1 에 없다** (이슈 #86). 목만 답한다.
 *
 * 🚨 **이 경로는 저장하지 않는다.** 검사지 사진에서 **적힌 것을 옮겨 오기만** 하고, 저장은
 *    보호자가 승인한 뒤 `POST /children/{cid}/health-safety`(승인 게이트 ㉡) 가 한다.
 *    최상위 `CLAUDE.md` §2 가 "알레르기·검진·건강 정보는 LLM 이 생성·추론·수정하지 않는다.
 *    보호자 직접 입력 또는 **의료 기록만**" 이라고 정했고, 검사지는 그 의료 기록이다 —
 *    허용되는 것은 **옮겨 적기**뿐이고 **채워 넣기**가 아니다.
 *
 * 🚨 그래서 이 타입의 필드는 대부분 `null` 을 허용한다. 못 읽은 칸은 `null` 로 오고,
 *    화면은 그 자리를 **비워 둔 채** 보호자에게 넘긴다 (기본값으로 넘기지 않는다 · §2).
 */
export interface SafetyScanCandidate {
  id: string;
  /** 못 읽었으면 `null`. 🚨 추측해 채우지 않는다. */
  type: string | null;
  label: string | null;
  category: string | null;
  severity: string | null;
  reactions: string[];
  /**
   * 🚨 **검사지에 적혀 있던 그 줄 그대로.** 이 제품은 추천에 근거를 달고 나가는데
   *    (PRODUCT.md), 보호자가 승인할 때 "무엇을 보고 이렇게 옮겼는지" 가 없으면 확인할 방법이
   *    없다. 원문이 없으면 `null` 이고, 그러면 화면은 그 줄을 **미리 고르지 않는다**.
   */
  source_text: string | null;
}

export interface SafetyScanResponse {
  /** 읽기 단위. 같은 사진을 다시 읽으면 새 값이다 (저장하는 것이 없어서 재생하지 않는다). */
  scan_id: string;
  candidates: SafetyScanCandidate[];
  /**
   * 🚨 **줄은 있는데 못 읽은 것의 수.** 0 이 아니면 화면이 그 사실을 그대로 말한다 —
   *    "다 읽었다" 고 넘기면 보호자가 빠진 항목을 모른 채 승인한다.
   */
  unreadable_count: number;
}

/* ── 11 아이 프로필 ──────────────────────────────────────────────────── */

/**
 * ⚠️ **계약서 v1 에 없다** (이슈 #75 · `apps/api` Owner 협의 + PM 결정 대기).
 *    계약서가 주는 것은 `PATCH /children/{cid}` 의 `nickname` · `birth_date` 둘뿐이고,
 *    읽는 엔드포인트(`GET /children/{cid}`)도 성별도 측정 로그도 없다.
 *    최상위 `CLAUDE.md` §2 의 "수집은 이름(별명)·나이·알레르기 여부까지" 도 함께 걸린다.
 *    그래서 **이 구역의 타입은 전부 목에서만 사는 제안된 모양**이고(아이 프로필 · 측정 기록 ·
 *    측정 기록 고치기까지), 서버가 붙기 전에 이 주석이 지워지면 안 된다 — 지워지는 순간
 *    계약서에 있는 것처럼 보인다.
 *
 * 🚨 **성별은 화면 표시 전용으로 제안했다.** 놀이·교육 추천이 성별로 갈리면 그건 이 제품이
 *    하려던 개인화(아이를 오래 알아온 것)가 아니라 통계다. Agent 컨텍스트에 넣지 않는다.
 *
 * ⚠️ **"밝히지 않음"(`unspecified`)을 뺐다 — 성별이 필수값이 됐다.** 최상위 CLAUDE.md §2 의
 *    "수집은 이름(별명)·나이·알레르기 여부까지" 를 넘는 쪽으로 한 걸음 더 간 것이라
 *    **#75 의 PM 결정 대상이 하나 늘었다**. 화면 표시 전용이라는 위 제약은 그대로다.
 *    ⚠️ 01 첫 진입은 아직 성별을 받지 않는다 — 새로 만든 아이의 성별을 무엇으로 둘지는
 *    서버 계약과 함께 정해야 한다 (지금은 목이 `male` 로 들고 있다).
 */
/**
 * 🚨 **`undisclosed` 가 기본값이다** (최상위 `CLAUDE.md` §2 — "성별의 기본값은
 *    '밝히지 않을래요' 다"). 한동안 `male | female` 둘만 두고 필수값으로 뒀다가(#75)
 *    되돌렸다 — 둘 중 하나를 고르게 만들면 **안 밝히는 선택지가 화면에서 사라진다.**
 *
 * 🚨 **"모름" 이 아니라 "밝히지 않음" 이다.** 보호자는 아이 성별을 알고 있고, 이 값은
 *    *우리에게 알려 줄지*를 고르는 것이다 — 문구를 "잘 모르겠어요" 로 쓰지 않는다.
 */
export type Gender = "male" | "female" | "undisclosed";

export interface ChildProfile {
  id: string;
  nickname: string;
  /** YYYY-MM-DD. */
  birth_date: string;
  /** 🚨 서버가 만든 문구. 프론트가 생일에서 계산하지 않는다 (CLAUDE.md §3). */
  age_display: string;
  gender: Gender;
  relation: Relation;
  role: "owner" | "member";
}

/** 셋 다 선택이다 — 고친 것만 보낸다. */
export interface UpdateChildRequest {
  nickname?: string;
  birth_date?: string;
  gender?: Gender;
}

export interface UpdateChildResponse {
  child: ChildProfile;
}

/**
 * 키 · 몸무게를 **잰 날 한 줄**. 🚨 지표가 아니라 기록이다.
 *
 * 🚨 **증감·백분위·또래 비교 필드를 여기에 만들지 않는다.** `DESIGN.md` 의
 *    "부모가 자기 아이를 지표로 보게 하지 않는다" 이고, "지난번보다 +2cm" 는 이 제품이
 *    하지 않기로 한 **발달 평가**다 (CLAUDE.md §2 · 스펙 아웃).
 */
export interface GrowthLog {
  id: string;
  /** YYYY-MM-DD. */
  measured_on: string;
  /** 🚨 한쪽만 재고 오는 날이 있다. 둘 다 null 인 행은 서버가 거부한다. */
  height_cm: number | null;
  weight_kg: number | null;
  /** 🚨 서버 문구("2주 전"). 없으면 그 자리를 비운다 — 프론트가 계산해 채우지 않는다. */
  measured_label?: string;
  note: string | null;
}

/** `?cursor=` 는 아직 쓰지 않는다 — 측정 기록은 한 화면에 다 들어오는 분량이다. */
export interface GrowthLogsResponse {
  items: GrowthLog[];
  next_cursor: string | null;
}

export interface CreateGrowthLogRequest {
  measured_on: string;
  height_cm?: number | null;
  weight_kg?: number | null;
}

export interface CreateGrowthLogResponse {
  log: GrowthLog;
}

/**
 * 🚨 **보낸 필드만 바꾼다.** 안 보낸 것은 그대로 둔다 — 두 보호자가 같은 화면을 열어 뒀을 때
 *    나중 저장이 남의 수정을 덮지 않게 (`UpdateChildRequest` 와 같은 규칙).
 *
 * 🚨 **`null` 은 "그날 그건 안 잰 것으로 되돌리기" 다.** 키를 아예 안 보내는 것(그대로 두기)과
 *    다르다 — 몸무게만 재고 온 날에 키를 잘못 적었으면 그 값을 **비울** 길이 있어야 한다.
 *    다만 둘 다 비면 잰 것이 없는 기록이라 서버가 거부한다.
 *
 * 🚨 **측정 기록은 승인 게이트가 아니다** (최상위 CLAUDE.md §2 — 게이트는 2곳이고 늘리지
 *    않는다). 되돌릴 수 없는 5개가 아니므로 `Idempotency-Key` 를 붙이지 않는다.
 */
export interface UpdateGrowthLogRequest {
  /** YYYY-MM-DD. */
  measured_on?: string;
  height_cm?: number | null;
  weight_kg?: number | null;
}

export interface UpdateGrowthLogResponse {
  log: GrowthLog;
}

/** `GET /children/{cid}/health-safety` — 보호자가 확정한 것만 들어 있다 (NF-03). */
export interface HealthSafetyListResponse {
  items: HealthSafety[];
  updated_at: string;
}

/* ── 02 이야기 하나 ──────────────────────────────────────────────────── */

/**
 * 🚨 발달 검사가 아니다. 보호자가 고른 값만 저장하고 AI 는 평가하지 않는다.
 *
 * ⚠️ **화면이 더 이상 부르지 않는다.** 02 에서 발달 문항을 뺐다 — 화면에 "발달 상태" 라는
 *    말이 서는 순간 발달 평가로 읽히고, 그건 이 제품이 안 만들기로 한 것이다 (최상위 §1).
 *    계약서에는 남아 있어 타입과 목은 유지한다 (서버가 지울지는 팀 결정).
 */
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

/**
 * 전부 선택이다. 모두 건너뛰어도 200 이다.
 *
 * ⚠️ **계약서 §05 의 본문과 달라졌다** (확정 전). 01 등록 화면을 "꼭 필요한 것" 만 남기고
 *    **고를 수 있는 것을 전부 이 화면으로 미루면서** 바뀌었다 —
 *    - 들어온 것: `relation`(01 에서 이동) · `gender` · `height_cm` · `weight_kg`
 *    - 빠진 것: `interests` · `dev_answers`
 *
 * 🚨 **`interests` 를 뺀 자리를 다른 것으로 채우지 않았다.** 관심사는 03 홈의 한 줄에서
 *    관찰로 쌓이는 값이고(그게 이 제품의 방식이다), 가입 첫날 칩으로 고른 여덟 개는
 *    **보호자가 짐작한 목록**이다. 발달 문항도 같은 이유로 뺐다 — 화면에 "발달 상태" 라는
 *    말이 서면 그때부터 발달 평가로 읽힌다 (최상위 §2 · 스펙 아웃).
 */
export interface OnboardingRequest {
  /**
   * 🚨 **아이가 아니라 나와 아이 사이의 값이다** (`parent_child` 행). `POST /children` 에서
   *    옮겨 왔다 — 01 은 되돌리기 어려운 것(별명·생일·법정대리인 동의)만 받는다.
   */
  relation?: Relation;
  gender?: Gender;
  /**
   * 🚨 **잰 날은 서버가 찍는다.** 프론트가 "오늘" 을 만들어 보내지 않는다 (CLAUDE.md §4 —
   *    날짜 계산은 서버가 한다). 다른 날 잰 값은 11-1 에서 날짜를 골라 적는다.
   * 🚨 둘 다 비면 아예 보내지 않는다 — 잰 것이 없는 기록을 만들지 않는다.
   */
  height_cm?: number;
  weight_kg?: number;
  /**
   * ⚠️ **화면에서 보내지 않는다.** 02 의 알레르기 구역이 11 아이 프로필과 같은 것이 되면서
   * (`components/safety-section.tsx`) 등록이 **승인 게이트 ㉡**(`POST /children/{cid}/health-safety`)
   * 로만 간다 — 알레르기의 쓰기 경로를 둘로 두지 않는다 (NF-03).
   *
   * 🚨 **그래서 `safety_status` 를 물을 자리가 지금 없다.** 목록이 0건인 것만으로는
   *    "확인했고 없다"(`none`)와 "아직 모른다"(`unknown`)를 가를 수 없는데, 계약서 §05 는
   *    후자를 `guards.safety_unknown` 으로 받아 Food Agent 실행 자체를 막는다 (최상위 §2).
   *    **어디서 그 선언을 받을지는 팀 결정이다** — 계약서에 남아 있어 타입만 유지한다.
   */
  safety_status?: SafetyStatus;
  safety?: OnboardingSafetyInput[];
  /**
   * ⚠️ 화면에서 보내지 않는다. 온보딩에서 한 줄을 또 받으면 03 홈의 입력과 같은 것을
   * 두 번 묻는 셈이라 뺐다 — 계약서에는 남아 있어서 타입만 유지한다.
   */
  one_line?: string;
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

/**
 * GET /consents?child_id= (계약서 §04). 10 설정의 동의 현황 + 철회 버튼이 쓴다.
 *
 * 🚨 `history` 는 증빙이라 삭제 엔드포인트가 없다 — 화면에도 "이력 지우기" 를 만들지 않는다.
 * 🚨 `effective` 는 **서버가 계산한 최신 상태**다. `history` 를 프론트에서 접어 현재 상태를
 *    다시 만들지 않는다 (append-only 라 같은 스코프에 여러 행이 있고, 접는 규칙이 두 벌이 된다).
 */
export interface ConsentHistoryEntry {
  scope: string;
  action: "granted" | "withdrawn";
  policy_version: string;
  acted_at: string;
}

export interface ConsentsResponse {
  effective: Record<string, boolean>;
  history: ConsentHistoryEntry[];
}

/* ── 10 설정 ─────────────────────────────────────────────────────────── */

/**
 * GET /children/{cid}/parents (계약서 §07). "함께 보는 보호자".
 *
 * 🚨 **승인 대기 상태가 없다.** 초대 링크를 수락하면 `parent_child` 행이 바로 생긴다 —
 *    화면에 "대기 중" 칸을 만들지 않는다 (계약서 §02 `GET /me`).
 */
export interface ChildParent {
  parent_id: string;
  nickname: string;
  relation: Relation;
  role: "owner" | "member";
  connected_at: string;
}

export interface ChildParentsResponse {
  parents: ChildParent[];
}

/**
 * POST /children/{cid}/invites — 정본은 `docs/api/invite-v1.md` 다.
 *
 * 🚨 **한 링크는 한 번만 쓴다.** `used_at` 이 찍히면 재사용 409 `invite_used`,
 *    기한이 지나면 410 이다. 화면이 "언제든 쓸 수 있는 링크" 처럼 보이게 하지 않는다.
 */
/**
 * `POST /children/{cid}/invites` 의 본문. 🚨 **비어 있는 것이 맞다.**
 *
 * 관계는 **받는 쪽이 수락 화면에서 고른다** (#89 · #96) — 잘못 찍으면 받는 쪽이 자기 프로필을
 * 고치러 가야 하고, 그 값은 기록마다 "누가 적었나" 로 남는다. `invite-v1.md` §3-1 도 발행 요청을
 * `{}` 로, 수락 요청이 `relation` 을 싣는 것으로 확정했다 (`InviteAcceptRequest`).
 */
export type InviteRequest = Record<string, never>;

export interface InviteResponse {
  /**
   * `docs/api/invite-v1.md` §3-1 확정. 형식·정규화는 `lib/invite-code.ts` — 그 파일 머리말에
   * 왜 링크가 아닌지 적어 뒀다.
   * 🚨 **보낼 때는 정규화한 값이다.** 화면의 `ABCD-1234` 는 읽기 편하라고 끊은 표시 형식이다.
   */
  invite_code: string;
  expires_at: string;
}

/**
 * GET /invites/{code} — 🔶 **아직 제안이다** (#96). 수락 **전에** 어느 아이인지
 * 보여주려고 신설했다. 코드 방식 자체는 확정됐고 **이 조회만 아직 열려 있다**
 * (`docs/api/invite-v1.md` §3-2 · §7 열린 결정 01).
 *
 * 🚨 **이 호출은 코드를 쓰지 않는다.** 확인 화면에서 그만둔 사람의 코드가 소비되면, 한 번만
 *    쓸 수 있는 코드라 다시 받아야 한다. 소비는 `accept` 하나만 한다.
 *
 * 🚨 **여기서 아이의 건강·알레르기를 내리지 않는다.** 아직 연결되지 않은 사람이고
 *    (`parent_child` 행이 없다), 코드만 알면 누구나 부를 수 있는 창구다 — 별명·나이와
 *    초대한 보호자까지다 (최상위 §2 개인정보 · 최소 수집).
 * 🚨 **그래서 이 경로도 시도 제한에 함께 걸린다.** 수락보다 **더 좋은 추측 도구**다 —
 *    맞는 코드를 찾는 데 계정 상태도 필요 없다.
 */
export interface InvitePreviewResponse {
  child: {
    nickname: string;
    /** 서버가 만든 문구. 프론트에서 다시 계산하지 않는다. */
    age_display: string;
  };
  /** 누가 불렀는지. 🚨 별명만 — 초대한 보호자의 다른 정보는 내리지 않는다. */
  invited_by: { nickname: string | null };
  expires_at: string;
}

/**
 * POST /invites/{code}/accept 의 본문.
 *
 * 🚨 **관계는 받는 쪽이 고른다.** 발행할 때 지정하지 않는다 (#89) — 아이와 어떤 사이인지는
 *    자기 입으로 말할 값이고, 그 값이 기록마다 "누가 적었나" 로 남는다.
 *    안 골랐으면 **필드를 아예 빼고 보낸다** (01 아이 등록과 같은 처리).
 */
export interface InviteAcceptRequest {
  relation?: Relation;
  /**
   * 만 19세 이상임을 받는 쪽이 직접 표시한 것 (약관 제5조 ④ · 제7조 ② · #166).
   *
   * 🔶 **이름과 기록 방식은 프론트 제안이다** — 서버 `AcceptInviteRequest` 에 아직 없다.
   *    지금 서버는 모르는 필드를 버리므로 먼저 실어 보내도 수락이 깨지지 않는다.
   * 🚨 **화면의 체크박스 값을 그대로 싣는다.** 상수 `true` 를 보내지 않는다 (`guardian_attested` 와 같은 이유).
   * 🚨 `guardian_attested` 가 아니다. 초대받은 사람은 법정대리인이 아닐 수 있어서 그 표시를
   *    받지 않는다 (약관 제7조 ②) — 나이만 받는다.
   */
  adult_attested: boolean;
}

/**
 * POST /invites/{code}/accept — 수락하면 `parent_child` 행이 **바로** 생긴다.
 * 🚨 승인 대기 상태가 없다 (계약서 §02 `GET /me`). 화면에 "대기 중" 칸을 만들지 않는다.
 *
 * ⚠️ **응답 모양이 계약서에 없다.** 화면은 수락한 뒤 그 아이 홈으로 가야 해서 `child_id`
 *    가 필요하다 — 없으면 `GET /me` 를 한 번 더 부르게 된다. 목이 이 모양으로 답한다.
 */
export interface InviteAcceptResponse {
  child_id: string;
  nickname: string;
  /** 서버가 만든 문구. 프론트에서 다시 계산하지 않는다. */
  age_display: string;
  role: "owner" | "member";
}

/**
 * POST /auth/withdraw — ⚠️ **계약서 v1 에도 `docs/api/auth-kakao-v1.md` 에도 없다.**
 * 그 문서 §1 이 "계정 탈퇴·파기 배치" 를 다루지 않는 것으로 미뤄 뒀다(노션). MSW 목 위에서만 돈다.
 *
 * 🚨 **유예가 없다. 누르면 그 자리에서 지워지고 되돌릴 수 없다** (PM 09-29 · #167).
 *    삭제 정책 정본 §9 가 MVP 를 복구(soft delete · restore UI) 없는 hard delete 로 정해 뒀다 —
 *    owner 가 탈퇴하면 아이 · 다른 보호자 연결 · 동의 기록까지 되살려야 해서 복구를 넣으면
 *    설계가 크게 늘어난다. 🚨 **화면에 "며칠 안에 돌아올 수 있다" 를 다시 만들지 말 것**:
 *    그 문구를 세우는 순간 서버가 지운 뒤에 부모가 돌아올 길을 찾는다.
 *
 * 서버가 이미 정해 둔 것(같은 문서 §4-2 · §5-3)은 **탈퇴 시 그 보호자의 세션을 전부
 * 무효화한다**는 것이다. ⚠️ 같은 문서의 `parent.deleted_at`(soft delete)은 **유예를 전제한
 * 서술**이라 지금 정책과 다르다 — 정본 §9 는 복구 없는 hard delete 다. 서버 문서 정리는
 * 백엔드 쪽에서 한다 (#182 리뷰).
 */
export interface WithdrawRequest {
  /**
   * 화면이 "지금 지워지고 되돌릴 수 없다" 를 보여줬다는 표시.
   *
   * 🚨 **서버는 이 값을 저장하지 않고 확인용으로만 쓴다** (#182 리뷰). 없거나 `false` 면
   *    `400 validation_failed` — 화면 버그로 잘못 나간 탈퇴 요청을 서버가 한 번 더 막는
   *    장치다. 값이 `true` 하나뿐인 것은 그래서다.
   * 🚨 **확정 버튼을 막는 것은 이 타입이 아니다** — 그건 읽음 표시(`disabled={!acknowledged}`)
   *    가 한다. 타입이 막는 것은 요청에 **다른 값을 싣는 코드**다.
   */
  acknowledged_immediate_deletion: true;
}

export interface WithdrawResponse {
  /** 지워진 시각. 화면이 계산하지 않는다 — 서버가 만든 값이다. */
  deleted_at: string;
}
