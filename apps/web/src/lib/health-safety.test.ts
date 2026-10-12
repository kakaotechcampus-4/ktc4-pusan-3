import { describe, expect, it } from "vitest";

import {
  SEVERITY_LABEL,
  isSeverityFor,
  severityFieldLabel,
  severityOptions,
} from "@/lib/health-safety";

/** DB `health_safety_severity_by_kind` 가 받는 값 (apps/api/app/domains/safety/models.py). */
const DB_ALLERGY = ["class_0", "class_1", "class_2", "class_3", "class_4", "class_5", "class_6"];
const DB_OTHER = ["mild", "moderate", "severe", "anaphylaxis"];

describe("health_safety 심각도는 DB 규칙을 따른다", () => {
  it("알레르기는 검사 Class 0~6 만 고르게 한다 (+ 모르겠어요)", () => {
    expect(severityOptions("allergy").map((o) => o.value)).toEqual(["unknown", ...DB_ALLERGY]);
  });

  it("그 밖의 종류는 DB 가 받는 값 안에서만 고르게 한다", () => {
    for (const type of ["chronic_disease", "behavioral", "environmental", "other_medical"]) {
      const values = severityOptions(type)
        .map((o) => o.value)
        .filter((v) => v !== "unknown");
      expect(values.length).toBeGreaterThan(0);
      for (const value of values) expect(DB_OTHER).toContain(value);
    }
  });

  it("🚨 알레르기에 가볍게~를, 다른 종류에 Class 를 보내면 DB 가 막는다 — 같은 규칙으로 막는다", () => {
    expect(isSeverityFor("allergy", "class_3")).toBe(true);
    expect(isSeverityFor("allergy", "mild")).toBe(false);
    expect(isSeverityFor("chronic_disease", "mild")).toBe(true);
    expect(isSeverityFor("chronic_disease", "class_3")).toBe(false);
    // "모르겠어요" 는 값이 아니다 — 보내지 않는다
    expect(isSeverityFor("allergy", "unknown")).toBe(false);
  });

  it("DB 가 받는 값은 전부 화면 이름이 있다 — 영문이 그대로 새지 않는다", () => {
    for (const value of [...DB_ALLERGY, ...DB_OTHER]) expect(SEVERITY_LABEL[value]).toBeTruthy();
  });

  it("알레르기 칸은 검사 등급이라고 말한다", () => {
    expect(severityFieldLabel("allergy")).toBe("검사 등급");
    expect(severityFieldLabel("chronic_disease")).toBe("얼마나 심한가요");
  });
});
