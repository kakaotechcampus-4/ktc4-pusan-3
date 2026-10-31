/**
 * 알레르기 · 건강 기록(`health_safety`)의 심각도 — 화면과 목이 같은 값을 본다.
 *
 * 🚨 **DB 규칙을 따른다** (`apps/api/app/domains/safety/models.py`, CHECK
 *    `health_safety_severity_by_kind`). 알레르기(`allergy`)의 심각도는 검사 결과의 Class
 *    (`class_0` ~ `class_6`)이고, 나머지 종류는 `mild` ~ `anaphylaxis` 다. 어긋난 조합은 DB 가
 *    막아서, 화면이 보내면 **알레르기 등록이 통째로 실패한다.**
 * 🚨 **"모르겠어요" 가 기본값이고, 고르면 값을 안 보낸다.** 심각도는 추측하면 안 되는 값이고
 *    (NF-03), 비워 두는 것이 없는 값을 지어내는 것보다 낫다.
 */

export interface SeverityOption {
  value: string;
  label: string;
}

const UNKNOWN: SeverityOption = { value: "unknown", label: "모르겠어요" };

/** 알레르기 — 검사 결과의 Class. 검사지에 적힌 글자 그대로 보여 준다. */
const ALLERGY_OPTIONS: SeverityOption[] = [
  UNKNOWN,
  ...Array.from({ length: 7 }, (_, n) => ({ value: `class_${n}`, label: `Class ${n}` })),
];

/** 그 밖의 종류. DB 는 `anaphylaxis` 도 받지만 보호자에게 고르게 하지 않는다. */
const GRADE_OPTIONS: SeverityOption[] = [
  UNKNOWN,
  { value: "mild", label: "가볍게" },
  { value: "moderate", label: "보통" },
  { value: "severe", label: "심하게" },
];

const ALLERGY_VALUES = new Set(ALLERGY_OPTIONS.slice(1).map((o) => o.value));
const GRADE_VALUES = new Set(["mild", "moderate", "severe", "anaphylaxis"]);

export function severityOptions(type: string): SeverityOption[] {
  return type === "allergy" ? ALLERGY_OPTIONS : GRADE_OPTIONS;
}

export function severityFieldLabel(type: string): string {
  return type === "allergy" ? "검사 등급" : "얼마나 심한가요";
}

/**
 * 심각도 칸 아래 설명. 알레르기만 있다 — 검사지 없이 아는 대로 적는 보호자도 많아서, `Class` 가
 * 무엇인지와 모르면 무엇을 고르는지를 말한다.
 *
 * 🚨 **Class 를 반응의 심한 정도처럼 말하지 않는다.** Class 는 혈액 속 특이 IgE 수치의 구간이라
 *    낮아도 심한 반응이 날 수 있다. "클수록 심해요" · "0~1 은 괜찮아요" 는 의료 판단으로 읽히고
 *    사실과도 다르다 (CLAUDE.md §1 안 만드는 것).
 * 🚨 **"모르겠어요" 를 고르라고 말한다.** 검사를 안 했으면 등급을 짐작해 고르지 않게 한다 —
 *    심각도는 추측하면 안 되는 값이다 (위 머리말 · NF-03).
 * 🚨 **마지막 문장은 Food Agent 의 규칙이다** — 등급과 상관없이(`class_0` 이어도) 거른다
 *    (`apps/api/app/agents/food/store/ports.py`). 그 규칙이 바뀌면 이 문장도 바꾼다.
 */
export function severityFieldHint(type: string): string | undefined {
  return type === "allergy"
    ? "알레르기 혈액검사 결과를 0부터 6까지 나눈 등급이에요. 검사를 안 했거나 잘 모르면 모르겠어요를 골라 주세요. 몇 등급이든 식단 추천에서는 똑같이 빼요."
    : undefined;
}

/** 목록 · 확인 화면에 쓰는 이름. DB 가 받는 값은 전부 있어서 영문이 그대로 새지 않는다. */
export const SEVERITY_LABEL: Record<string, string> = {
  ...Object.fromEntries(ALLERGY_OPTIONS.slice(1).map((o) => [o.value, o.label])),
  mild: "가볍게",
  moderate: "보통",
  severe: "심하게",
  anaphylaxis: "아나필락시스",
};

/** DB CHECK `health_safety_severity_by_kind` 와 같은 규칙. 목이 서버 대신 막는 데도 쓴다. */
export function isSeverityFor(type: string, severity: string): boolean {
  return type === "allergy" ? ALLERGY_VALUES.has(severity) : GRADE_VALUES.has(severity);
}

/* ── 길이 ─────────────────────────────────────────────────────────────── */

/**
 * 서버가 받는 길이 (`apps/api/app/api/v1/schemas/health_safety.py`). 넘기면 400 이다.
 *
 * 🚨 **화면이 먼저 막는다.** 서버의 400 은 화면에서 일반 실패로 그려져 "다시 눌러 주세요" 가
 *    되는데, 같은 값을 다시 보내면 같은 400 이다. 이름과 메모는 입력이 막고(`maxLength`),
 *    증상은 쉼표로 한 칸에 적어서 입력이 못 막으니 확인할 때 막는다 (`reactionsProblem`).
 */
export const HEALTH_SAFETY_LIMITS = {
  label: 100,
  notes: 500,
  reactions: 20,
  reaction: 100,
} as const;

/** 쉼표로 나눠 적은 증상. 빈 칸은 뺀다 — 서버는 빈 증상을 받지 않는다. */
export function splitReactions(text: string): string[] {
  return text
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
}

/** 증상이 서버가 받는 길이를 넘으면 그 사유, 아니면 `null`. */
export function reactionsProblem(reactions: string[]): string | null {
  if (reactions.length > HEALTH_SAFETY_LIMITS.reactions) {
    return `증상은 ${HEALTH_SAFETY_LIMITS.reactions}개까지 적을 수 있어요`;
  }
  if (reactions.some((r) => r.length > HEALTH_SAFETY_LIMITS.reaction)) {
    return `증상 하나는 ${HEALTH_SAFETY_LIMITS.reaction}자까지 적을 수 있어요`;
  }
  return null;
}
