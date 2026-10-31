import { describe, expect, it } from "vitest";

import {
  HEALTH_SAFETY_LIMITS,
  SEVERITY_LABEL,
  isSeverityFor,
  reactionsProblem,
  severityFieldHint,
  severityFieldLabel,
  severityOptions,
  splitReactions,
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

  it("설명은 알레르기 칸에만 붙는다 (#570)", () => {
    expect(severityFieldHint("allergy")).toBeTruthy();
    for (const type of ["chronic_disease", "behavioral", "environmental", "other_medical"]) {
      expect(severityFieldHint(type)).toBeUndefined();
    }
  });

  // Class 는 혈액 속 특이 IgE 수치의 구간이라 반응의 심한 정도가 아니다. 등급으로 위험을 가늠하는
  // 말은 의료 판단으로 읽힌다 (CLAUDE.md §1 안 만드는 것).
  it("🚨 설명은 Class 로 반응의 심한 정도나 안전을 가늠하지 않는다", () => {
    const hint = severityFieldHint("allergy") ?? "";
    for (const word of ["심해", "심한", "위험", "안전", "괜찮", "클수록", "높을수록", "낮으면"]) {
      expect(hint).not.toContain(word);
    }
  });
});

/** 서버 스키마(`apps/api/app/api/v1/schemas/health_safety.py`)가 받는 길이 (#369). */
describe("health_safety 길이는 서버가 받는 만큼이다", () => {
  // ⚠️ 서버와 맞는지를 잡는 테스트가 아니다 — 서버가 바뀌어도 깨지지 않는다. 상수를 바꾸는 순간
  //    깨져서 "서버 스키마(`schemas/health_safety.py`)도 같이 봤는가" 를 묻는 알림이다.
  it("길이 상수를 바꾸면 서버 스키마(#369)도 같이 확인한다", () => {
    expect(HEALTH_SAFETY_LIMITS).toEqual({ label: 100, notes: 500, reactions: 20, reaction: 100 });
  });

  it("쉼표로 적은 증상은 앞뒤 공백을 떼고 빈 칸을 뺀다 — 서버는 빈 증상을 받지 않는다", () => {
    expect(splitReactions(" 두드러기, ,기침 ,")).toEqual(["두드러기", "기침"]);
    expect(splitReactions("")).toEqual([]);
  });

  it("증상 개수와 길이가 서버 제한 안이면 사유가 없다", () => {
    expect(reactionsProblem(Array.from({ length: 20 }, () => "가".repeat(100)))).toBeNull();
  });

  it("🚨 증상이 21개거나 한 증상이 101자면 보내기 전에 막는다 — 서버는 400 이다", () => {
    expect(reactionsProblem(Array.from({ length: 21 }, (_, n) => `증상${n}`))).toBe(
      "증상은 20개까지 적을 수 있어요",
    );
    expect(reactionsProblem(["가".repeat(101)])).toBe("증상 하나는 100자까지 적을 수 있어요");
  });
});
