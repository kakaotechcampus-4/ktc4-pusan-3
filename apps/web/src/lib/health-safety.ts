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
