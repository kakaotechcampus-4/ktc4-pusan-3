/**
 * 계약서 v1 기준 시드 데이터.
 *
 * 🚨 여기 문장은 전부 지어낸 것이다. 실제 사용자 발화나 아이 정보를 붙여넣지 않는다
 *    (저장소가 public 이다 — 최상위 CLAUDE.md §9).
 *
 * 날짜를 여기서 계산하는 이유 — 이 파일은 "서버 역할"이다. 서버가 만들어 내려주는
 * observed_label · age_display · state_reason 을 목도 똑같이 만들어야 화면이 그걸
 * 그대로 그리는지 확인할 수 있다. 화면 코드에서 날짜를 계산하지 않는 규칙과 충돌하지 않는다.
 */

import type {
  Affinity,
  CalendarDay,
  CalendarEvent,
  Evidence,
  ChildProfile,
  GeneralSuggestion,
  GrowthLog,
  HealthSafety,
  SafetyScanResponse,
  HomeResponse,
  Me,
  Observation,
  ObservationHealth,
  ObservationPromotable,
  Suggestion,
} from "@/lib/api/types";

/* ── 날짜 ─────────────────────────────────────────────────────────────── */

const DAY_MS = 24 * 60 * 60 * 1000;

/**
 * 🚨 `toISOString()` 을 쓰지 않는다. UTC 로 접혀서 한국 시간 자정~오전 9시 사이에는
 *    **하루가 밀린다** — 그러면 목이 만든 "오늘" 이 화면이 보는 오늘과 달라서,
 *    캘린더에서 오늘 칸에 표식이 안 찍히는 것처럼 보인다 (`lib/format.ts` 의 같은 주의).
 */
function iso(date: Date): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

export function daysAgo(n: number): string {
  return iso(new Date(Date.now() - n * DAY_MS));
}

export function hoursFromNow(n: number): string {
  return new Date(Date.now() + n * 60 * 60 * 1000).toISOString();
}

/** 서버가 만드는 표시 문구. 프론트가 아니라 여기서 만든다. */
function observedLabel(days: number): string {
  if (days === 0) return "오늘";
  if (days === 1) return "어제";
  if (days < 7) return `${days}일 전`;
  return `${Math.floor(days / 7)}주 전`;
}

/* ── 식별자 ───────────────────────────────────────────────────────────── */

export const CHILD_ID = "c1";
export const PARENT_ID = "p1";
export const MOCK_TOKEN = "mock-token-do-not-use-in-production";

/* ── 보호자 · 아이 ────────────────────────────────────────────────────── */

export const me: Me = {
  id: PARENT_ID,
  nickname: "지은",
  children: [
    {
      child_id: CHILD_ID,
      nickname: "민준",
      age_display: "만 4세",
      relation: "mother",
      role: "owner",
      consent_required: [],
    },
  ],
};

/** consent 시나리오에서 쓴다 — 필수 동의가 비어 있으면 그 아래 저장이 전부 막힌다. */
export const meNeedingConsent: Me = {
  ...me,
  children: [{ ...me.children[0], consent_required: ["child_health"] }],
};

/* ── 관찰 ─────────────────────────────────────────────────────────────── */

function promotable(
  id: string,
  kind: ObservationPromotable["kind"],
  subject: string,
  rawText: string,
  days: number,
  affinity: ObservationPromotable["affinity"],
): ObservationPromotable {
  return {
    id,
    child_id: CHILD_ID,
    kind,
    raw_text: rawText,
    subject,
    polarity: 1,
    strong_signals: ["또 찾아요"],
    confidence_source: "parent_direct",
    status: "active",
    observed_from: daysAgo(days),
    observed_to: daysAgo(days),
    observed_label: observedLabel(days),
    affinity,
    domain_fields: {},
  };
}

const eggAffinityRef = { id: "a_12", merge_key: "계란 반찬", state: "confirmed" as const };

export const observations: ObservationPromotable[] = [
  promotable(
    "o_1",
    "observation_food",
    "계란말이",
    "저녁에 계란말이를 또 찾았어요",
    0,
    eggAffinityRef,
  ),
  promotable(
    "o_2",
    "observation_food",
    "계란말이",
    "계란말이만 두 그릇 먹었어요",
    2,
    eggAffinityRef,
  ),
  promotable(
    "o_3",
    "observation_food",
    "계란말이",
    "반찬으로 계란말이를 골랐어요",
    5,
    eggAffinityRef,
  ),
  promotable("o_4", "observation_activity", "물놀이", "물놀이터에서 안 나오려고 했어요", 3, {
    id: "a_20",
    merge_key: "물놀이",
    state: "candidate",
  }),
];

/** 🚨 health 는 모양이 다르다 — subject · polarity · affinity 키 자체가 없다. */
export const healthObservation: ObservationHealth = {
  id: "o_h1",
  child_id: CHILD_ID,
  kind: "observation_health",
  raw_text: "자다가 기침을 몇 번 했어요",
  confidence_source: "parent_direct",
  status: "active",
  observed_from: daysAgo(1),
  observed_to: daysAgo(1),
  observed_label: observedLabel(1),
  domain_fields: {
    symptom: ["기침"],
    severity: "mild",
    observed_time: "밤",
  },
};

/* ── Child Memory ─────────────────────────────────────────────────────── */

export const affinities: Affinity[] = [
  {
    kind: "profile_affinity",
    id: "a_12",
    merge_key: "계란 반찬",
    domain: "food",
    state: "confirmed",
    polarity: 1,
    strength: 0.82,
    last_observed_on: daysAgo(0),
    observation_count: 3,
    state_reason: "서로 다른 3일에 기록됐어요",
    is_stale: false,
    source_refs: [
      { kind: "observation_food", id: "o_1" },
      { kind: "observation_food", id: "o_2" },
      { kind: "observation_food", id: "o_3" },
    ],
  },
  {
    kind: "profile_affinity",
    id: "a_20",
    merge_key: "물놀이",
    domain: "activity",
    state: "candidate",
    polarity: 1,
    strength: 0.41,
    last_observed_on: daysAgo(3),
    observation_count: 1,
    state_reason: "아직 한 번 봤어요",
    is_stale: false,
    source_refs: [{ kind: "observation_activity", id: "o_4" }],
  },
];

/** stale 시나리오 — 6개월 넘은 기억만 남았을 때. 단독 근거로 쓰면 안 된다 (NF-08). */
export const staleAffinities: Affinity[] = affinities.map((a) => ({
  ...a,
  last_observed_on: daysAgo(220),
  is_stale: true,
  state_reason: "마지막으로 본 지 7개월이 지났어요",
}));

export const healthSafety: HealthSafety[] = [
  {
    kind: "health_safety",
    id: "hs_1",
    type: "allergy",
    label: "우유",
    aliases: ["유제품"],
    category: "식품",
    severity: "moderate",
    reactions: ["두드러기"],
    management: { avoid: true },
    notes: null,
    created_by: { parent_id: PARENT_ID, nickname: "지은" },
    updated_at: hoursFromNow(-72),
  },
];

/**
 * 보호자가 방금 확정한 안전 정보 (승인 게이트 ㉡ 응답).
 * 🚨 서버가 만드는 필드(id · created_by · updated_at)는 여기서 채운다 — 요청에 없는 값이다.
 */
export function newHealthSafety(input: {
  type: string;
  label: string;
  category?: string;
  severity?: string | null;
  reactions?: string[];
  notes?: string | null;
}): HealthSafety {
  return {
    kind: "health_safety",
    id: `hs_${Date.now()}`,
    type: input.type,
    label: input.label,
    aliases: [],
    category: input.category ?? "기타",
    severity: input.severity ?? null,
    reactions: input.reactions ?? [],
    management: { avoid: true },
    notes: input.notes ?? null,
    created_by: { parent_id: PARENT_ID, nickname: me.nickname ?? "" },
    updated_at: hoursFromNow(0),
  };
}

/* ── 11 알레르기 검사지 읽기 ─────────────────────────────────────────── */

/**
 * ⚠️ `POST /children/{cid}/health-safety/scan` 은 계약서 v1 에 없다 (이슈 #86).
 *
 * 🚨 **여기 있는 것은 "검사지에 적혀 있던 것" 을 흉내 낸 값이다.** 실제 검사지도, 실제 아이
 *    정보도 아니다 (저장소가 public · 최상위 §9).
 *
 * 🚨 **일부러 덜 읽은 줄을 섞어 뒀다.** 화면이 못 읽은 칸을 **비워서** 보호자에게 넘기는지
 *    확인하려면 목이 완벽하게 읽어 주면 안 된다 — 목의 존재 이유가 그것이다 (§7 머리말).
 *      · `sc_1` 전부 읽음 → 미리 골라 둔다
 *      · `sc_2` 분류를 못 읽음 → 보호자가 채워야 고를 수 있다
 *      · `sc_3` 원문이 없음 → 무엇을 보고 옮겼는지 못 대니 미리 고르지 않는다
 *      · `unreadable_count` 2 → 줄은 있는데 통째로 못 읽은 것이 둘
 */
export const safetyScan: SafetyScanResponse = {
  scan_id: "scan_1",
  candidates: [
    {
      id: "sc_1",
      type: "allergy",
      label: "달걀흰자",
      category: "식품",
      severity: "moderate",
      reactions: ["두드러기"],
      source_text: "Egg white  class 3  (3.9 kU/L)",
    },
    {
      id: "sc_2",
      type: "allergy",
      label: "땅콩",
      category: null,
      severity: "severe",
      reactions: [],
      source_text: "Peanut  class 4",
    },
    {
      id: "sc_3",
      type: "allergy",
      label: "집먼지진드기",
      category: "환경",
      severity: null,
      reactions: [],
      source_text: null,
    },
    {
      // 🚨 이미 등록된 항목이다. 화면이 미리 걸러 내는지 확인하는 줄 —
      //    안 걸러 내면 승인하고 나서 409 를 본다.
      id: "sc_4",
      type: "allergy",
      label: "우유",
      category: "식품",
      severity: "moderate",
      reactions: ["두드러기"],
      source_text: "Milk  class 3",
    },
    /**
     * 🚨 **잘 읽은 줄이 여기부터 여럿이다.** 검사지 한 장에는 보통 열 줄 넘게 찍히는데,
     *    후보가 서넛뿐이면 **"잘 읽었어요" 를 왜 접어 두는지가 화면에서 안 보인다** —
     *    확인이 필요한 두 줄이 화면 위에 그냥 있고, 접힘이 있으나 마나가 된다.
     *    11-2 가 두 무리로 가르는 이유를 목이 실제로 만들어 줘야 한다.
     * 🚨 이름은 전부 지어낸 것이다 (실제 검사지도 실제 아이 정보도 아니다 · 최상위 §9).
     */
    {
      id: "sc_5",
      type: "allergy",
      label: "새우",
      category: "식품",
      severity: "moderate",
      reactions: ["두드러기"],
      source_text: "Shrimp  class 3",
    },
    {
      id: "sc_6",
      type: "allergy",
      label: "고등어",
      category: "식품",
      severity: "mild",
      reactions: [],
      source_text: "Mackerel  class 2",
    },
    {
      id: "sc_7",
      type: "allergy",
      label: "밀",
      category: "식품",
      severity: "mild",
      reactions: [],
      source_text: "Wheat  class 2",
    },
    {
      id: "sc_8",
      type: "allergy",
      label: "대두",
      category: "식품",
      severity: "mild",
      reactions: [],
      source_text: "Soybean  class 2",
    },
    {
      id: "sc_9",
      type: "allergy",
      label: "자작나무 꽃가루",
      category: "환경",
      severity: "mild",
      reactions: ["재채기"],
      source_text: "Birch pollen  class 2",
    },
    {
      id: "sc_10",
      type: "allergy",
      label: "고양이 비듬",
      category: "환경",
      severity: "mild",
      reactions: [],
      source_text: "Cat dander  class 2",
    },
  ],
  unreadable_count: 2,
};

/* ── 11 아이 프로필 · 측정 로그 ───────────────────────────────────────── */

/**
 * ⚠️ `GET /children/{cid}` 는 계약서 v1 에 없다 (이슈 #75). 여기가 그 제안의 유일한 구현이다.
 * 🚨 `age_display` 는 **서버 문구**라 목이 만든다 — 화면은 생일에서 나이를 계산하지 않는다.
 */
export const childProfile: ChildProfile = {
  id: CHILD_ID,
  nickname: "민준",
  birth_date: "2021-04-02",
  age_display: "만 4세",
  gender: "unspecified",
  relation: "mother",
  role: "owner",
};

/** 🚨 `measured_label` 도 서버 문구다 (`observed_label` 과 같은 처리). */
function growthLog(
  id: string,
  days: number,
  height: number | null,
  weight: number | null,
): GrowthLog {
  return {
    id,
    measured_on: daysAgo(days),
    height_cm: height,
    weight_kg: weight,
    measured_label: observedLabel(days),
    note: null,
  };
}

/**
 * 🚨 **증감·백분위를 담지 않는다.** 이 배열은 "잰 날 목록" 이지 성장 곡선의 데이터가 아니다
 *    (`DESIGN.md` — 부모가 자기 아이를 지표로 보게 하지 않는다).
 * 🚨 한쪽만 잰 날이 섞여 있다 — 화면이 null 한쪽을 제대로 그리는지 여기서 걸린다.
 */
/**
 * 🚨 **한쪽만 잰 날이 섞여 있어야 한다** (`g_5`). 그 날은 키 그래프에 점이 없고 몸무게에만
 *    있는데, 빈 칸을 0 이나 직전 값으로 채우는 버그는 이 픽스처가 아니면 화면에서 안 보인다.
 * 🚨 **잰 간격이 고르지 않다.** 가로축이 시간 축이라 간격이 그대로 그려지는데, 고른 간격만
 *    넣어 두면 축을 범주로 그려 놓고도 맞아 보인다 (`GrowthChart` 머리말).
 */
export const growthLogs: GrowthLog[] = [
  growthLog("g_6", 12, 104.2, 17.1),
  growthLog("g_5", 47, null, 16.8),
  growthLog("g_4", 104, 101.5, 16.2),
  growthLog("g_3", 190, 99.8, 15.7),
  growthLog("g_2", 285, 97.1, 15.1),
  growthLog("g_1", 372, 94.6, 14.4),
];

/** 🚨 서버가 채우는 값(id · measured_label)은 여기서 만든다 — 요청에 없는 값이다. */
export function newGrowthLog(input: {
  measured_on: string;
  height_cm?: number | null;
  weight_kg?: number | null;
}): GrowthLog {
  const days = Math.max(
    0,
    Math.round((Date.now() - new Date(`${input.measured_on}T00:00:00`).getTime()) / DAY_MS),
  );
  return {
    id: `g_${Date.now()}`,
    measured_on: input.measured_on,
    height_cm: input.height_cm ?? null,
    weight_kg: input.weight_kg ?? null,
    measured_label: observedLabel(days),
    note: null,
  };
}

/* ── 제안 ─────────────────────────────────────────────────────────────── */

function evidenceFrom(a: Affinity): Evidence {
  return {
    ref: { kind: "profile_affinity", id: a.id },
    label: a.merge_key,
    observed_to: a.last_observed_on,
    confidence_source: "parent_direct",
  };
}

/** 🚨 evidence 0건인 suggestion 은 만들지 않는다 — 그건 서버가 버리고 scarcity 로 내린다. */
export const suggestions: Suggestion[] = [
  {
    id: "s_1",
    child_id: CHILD_ID,
    agent: "food",
    kind: "personalized",
    content: "계란말이에 시금치를 조금 섞어 보세요",
    reason: {
      why_this: "계란 반찬을 서로 다른 3일에 찾았어요",
      why_now: "오늘 급식에 계란 반찬이 없어요",
    },
    status: "draft",
    expires_at: hoursFromNow(24),
    feedback: null,
    source_refs: [{ kind: "profile_affinity", id: "a_12" }],
    evidence: [evidenceFrom(affinities[0])],
  },
  {
    id: "s_2",
    child_id: CHILD_ID,
    agent: "activity",
    kind: "personalized",
    content: "주말에 실내 물놀이장은 어떨까요",
    reason: {
      why_this: "물놀이에서 오래 머물렀어요",
      why_now: "이번 주말 일정이 비어 있어요",
    },
    status: "draft",
    expires_at: hoursFromNow(24),
    feedback: null,
    source_refs: [{ kind: "profile_affinity", id: "a_20" }],
    evidence: [evidenceFrom(affinities[1])],
  },
];

/**
 * 근거가 부족할 때 개인화 **대신** 나가는 또래 기준 추천 (CLAUDE.md §2).
 *
 * 🚨 `evidence` 필드가 없다 — 일반 추천은 근거 0행이 정상이고, 그래서 개인화와 **타입이 다르다.**
 * 🚨 `basis` 는 서버가 만든 문구다. 목에서도 프론트가 "또래 기준" 을 지어내지 않게 여기 둔다.
 */
export const generalSuggestions: GeneralSuggestion[] = [
  {
    id: "g_1",
    child_id: CHILD_ID,
    agent: "activity",
    kind: "general",
    content: "블록 쌓기처럼 손을 많이 쓰는 놀이를 15분쯤 해 보세요",
    basis: "36개월 또래가 자주 찾는 놀이예요",
  },
  {
    id: "g_2",
    child_id: CHILD_ID,
    agent: "food",
    kind: "general",
    content: "국물 없이 집어 먹는 반찬을 한 가지 곁들여 보세요",
    basis: "36개월 또래의 식사에서 흔한 형태예요",
  },
];

/**
 * stale 시나리오 — 근거가 전부 6개월을 넘겼다.
 *
 * 🚨 `Evidence.is_stale` 은 **계약서 v1 에 아직 없는 필드**다 (types.ts 의 주석 참고).
 *    프론트는 날짜를 계산하지 않으므로 이 판정은 서버가 내려줘야 하고, 그 전까지는
 *    점선 근거 칩(NF-08)을 확인할 방법이 목뿐이다.
 */
export const staleSuggestion: Suggestion = {
  ...suggestions[1],
  evidence: [{ ...evidenceFrom(staleAffinities[1]), is_stale: true }],
};

/* ── 홈 ───────────────────────────────────────────────────────────────── */

export const home: HomeResponse = {
  observation_count: observations.length + 1,
  week_count: 4,
  upcoming_count: 1,
  today: [
    { kind: "meal", title: "급식 · 미역국, 김, 두부조림", origin: "어린이집" },
    { kind: "event", event_id: "e_3", title: "내일 물놀이 · 준비물 3개" },
  ],
  highlight: {
    text: "계란 반찬을 찾은 지 5일째예요.",
    state_reason: "서로 다른 3일에 기록됐어요",
    ref: { kind: "profile_affinity", id: "a_12" },
  },
  agent_prompts: [
    { agent: "food", text: "오늘 저녁 뭐 할지 같이 정하기" },
    { agent: "activity", text: "주말에 뭐 하고 놀지 정하기" },
  ],
};

/** 🚨 기억 0건 — highlight 가 null 이고 화면은 빈 상태를 그린다. 사과문이 아니라 건수를 보여준다. */
export const emptyHome: HomeResponse = {
  observation_count: 0,
  week_count: 0,
  upcoming_count: 0,
  today: [],
  highlight: null,
  agent_prompts: [],
};

/* ── 캘린더 초안 (승인 게이트 ㉠) ─────────────────────────────────────── */

export function draftEvent(overrides: Partial<CalendarEvent> = {}): CalendarEvent {
  return {
    id: "e_draft_1",
    title: "실내 물놀이장",
    event_type: "episodic",
    starts_at: hoursFromNow(72),
    ends_at: null,
    all_day: false,
    category: "activity",
    status: "draft",
    created_by: "agent",
    source_notice_id: null,
    source_refs: [{ kind: "suggestion", id: "s_2" }],
    items: [{ item_id: "i_1", item_name: "수영복", is_prepared: false, prepared_at: null }],
    reminders: [],
    ...overrides,
  };
}

/* ── 07 기억 ──────────────────────────────────────────────────────────── */

/**
 * 관찰 목록은 4개 도메인이 섞여 내려온다 (`?domain=` 생략 시 4개 테이블 병합).
 * 필터 칩이 실제로 무언가를 걸러내려면 도메인이 셋 이상 있어야 해서 교육 관찰을 하나 더 둔다.
 */
export const educationObservation: ObservationPromotable = promotable(
  "o_5",
  "observation_education",
  "숫자 세기",
  "블록을 세면서 열까지 갔어요",
  6,
  null,
);

/** 4개 테이블을 `observed_to DESC` 로 병합한 모양 — 서버가 하는 일을 목도 그대로 한다. */
export const allObservations: Observation[] = [
  ...observations,
  educationObservation,
  healthObservation,
].sort((a, b) => (a.observed_to < b.observed_to ? 1 : -1));

/**
 * 07 피드백 탭이 평가할 제안 목록.
 * ⚠️ `GET /children/{cid}/suggestions` 는 계약서 v1 에 아직 없다 (types.ts 의 ⚠️).
 */
export const receivedSuggestions: Suggestion[] = [
  { ...suggestions[0], id: "s_past_1", status: "approved", feedback: "child_liked" },
  { ...suggestions[1], id: "s_past_2", status: "rejected", feedback: null },
];

/* ── 09 캘린더 ───────────────────────────────────────────────────────── */

/** 🚨 캘린더에 서는 일정은 전부 승인이 끝난 것이다 (`confirmed`). draft 는 여기 오지 않는다. */
export const confirmedEvent: CalendarEvent = {
  id: "e_3",
  title: "물놀이",
  event_type: "episodic",
  starts_at: `${daysAgo(-2)}T10:00:00+09:00`,
  ends_at: null,
  all_day: false,
  category: "institution",
  status: "confirmed",
  created_by: "agent",
  source_notice_id: null,
  source_refs: [],
  items: [
    { item_id: "i_1", item_name: "수건", is_prepared: false, prepared_at: null },
    { item_id: "i_2", item_name: "수영복", is_prepared: true, prepared_at: hoursFromNow(-20) },
  ],
  reminders: [],
};

/** 일기는 관찰이 아니다 — 별도 저장소에 둔다 (계약서 §09). 날짜 → 본문·사진. */
const diaries = new Map<string, { text: string; image_urls: string[] }>([
  [
    daysAgo(1),
    {
      text: "저녁에 블록을 한참 쌓다가 무너지니까 웃었다.\n자기 전에 물을 두 컵 마셨다.",
      image_urls: [],
    },
  ],
]);

/** 🚨 프로필 승격이 일어난 날. 프론트가 관찰 건수로 역산할 수 없는 값이라 목이 들고 있다. */
const profileChanged = new Map<string, number>([[daysAgo(0), 1]]);

export function readDiary(date: string): { text: string; image_urls: string[] } | null {
  return diaries.get(date) ?? null;
}

export function writeDiary(date: string, value: { text: string; image_urls: string[] }): void {
  if (value.text.trim() === "") diaries.delete(date);
  else diaries.set(date, value);
}

/** 테스트용 — 목은 프로세스 수명만큼 살아 있다 (apps/web/CLAUDE.md §8). */
export function resetDiaries(): void {
  diaries.clear();
  diaries.set(daysAgo(1), {
    text: "저녁에 블록을 한참 쌓다가 무너지니까 웃었다.\n자기 전에 물을 두 컵 마셨다.",
    image_urls: [],
  });
}

/** 일정이 걸린 날. 서버가 `starts_at` 을 날짜로 접어 내리는 자리다. */
function eventDate(event: CalendarEvent): string {
  return event.starts_at.slice(0, 10);
}

export function calendarEventsOn(date: string): CalendarEvent[] {
  return [confirmedEvent].filter((event) => eventDate(event) === date);
}

export function calendarObservationsOn(date: string): Observation[] {
  return allObservations.filter((observation) => observation.observed_to === date);
}

/**
 * 월 조회. 🚨 `has_event` 는 `confirmed` 만 센다 — draft 는 아직 캘린더에 쓴 것이 아니다.
 */
export function calendarMonth(month: string): CalendarDay[] {
  const dates = new Set<string>();
  for (const observation of allObservations) {
    if (observation.observed_to.startsWith(month)) dates.add(observation.observed_to);
  }
  for (const event of [confirmedEvent]) {
    if (eventDate(event).startsWith(month)) dates.add(eventDate(event));
  }
  for (const date of diaries.keys()) if (date.startsWith(month)) dates.add(date);
  for (const date of profileChanged.keys()) if (date.startsWith(month)) dates.add(date);

  return [...dates].sort().map((date) => ({
    date,
    has_diary: diaries.has(date),
    has_event: calendarEventsOn(date).length > 0,
    has_image: (diaries.get(date)?.image_urls.length ?? 0) > 0,
    observation_count: calendarObservationsOn(date).length,
    profile_changed_count: profileChanged.get(date) ?? 0,
  }));
}
