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

/** 4버튼 교정 (CLAUDE.md §5 Correction). */
export type CorrectionVerdict = "confirm" | "once_only" | "outdated" | "wrong";

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
  content: string;
  reason: { why_this: string; why_now: string };
  status: SuggestionStatus;
  /** 생성 +24h. 승인 없는 draft 는 여기서 만료된다 (NF-07). */
  expires_at: string;
  feedback: unknown | null;
  source_refs: Ref[];
  evidence: Evidence[];
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
