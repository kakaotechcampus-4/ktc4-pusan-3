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
  CalendarEvent,
  Evidence,
  HealthSafety,
  HomeResponse,
  Me,
  ObservationHealth,
  ObservationPromotable,
  Suggestion,
} from "@/lib/api/types";

/* ── 날짜 ─────────────────────────────────────────────────────────────── */

const DAY_MS = 24 * 60 * 60 * 1000;

function iso(date: Date): string {
  return date.toISOString().slice(0, 10);
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
    state_reason: "서로 다른 3일에 관찰됐어요",
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
    state_reason: "서로 다른 3일에 관찰됐어요",
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
