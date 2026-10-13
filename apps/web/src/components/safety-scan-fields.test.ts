import { describe, expect, it } from "vitest";

import { isSelectable, needsReview, reviewReason, toRow } from "@/components/safety-scan-fields";
import type { SafetyScanCandidate } from "@/lib/api";

const candidate: SafetyScanCandidate = {
  id: "sc",
  type: "allergy",
  label: "복숭아",
  severity: null,
  reactions: [],
  source_text: "Peach  class 2",
};

describe("검사지 한 줄의 심각도", () => {
  it("검사 Class 는 그대로 옮긴다", () => {
    expect(toRow({ ...candidate, severity: "class_2" }).severity).toBe("class_2");
  });

  it("🚨 알레르기에 쓸 수 없는 값이면 비워서 둔다 — 등록이 통째로 막히는 것보다 낫다", () => {
    // DB 는 알레르기에 mild 를 받지 않는다. 심각도는 필터가 쓰지 않으니 빈 채로 등록되는 편이 안전하다.
    expect(toRow({ ...candidate, severity: "moderate" }).severity).toBeNull();
  });
});

/** 검사지에서 옮긴 글은 입력의 `maxLength` 를 거치지 않고 들어온다 (#369 · 서버가 받는 길이). */
describe("검사지 한 줄의 길이", () => {
  it("🚨 서버가 받지 않는 이름이면 미리 고르지 않고 확인이 필요한 줄로 둔다", () => {
    const row = toRow({ ...candidate, label: "가".repeat(101) });
    expect(row.checked).toBe(false);
    expect(needsReview(row)).toBe(true);
    expect(isSelectable(row)).toBe(false);
    expect(reviewReason(row)).toBe("옮겨 온 글이 너무 길어요. 검사지를 보고 줄여주세요.");
  });

  it("증상이 서버 제한을 넘어도 같다", () => {
    const row = toRow({ ...candidate, reactions: Array.from({ length: 21 }, (_, n) => `r${n}`) });
    expect(row.checked).toBe(false);
    expect(needsReview(row)).toBe(true);
  });

  it("제한 안이면 그대로 미리 고른다", () => {
    const row = toRow({ ...candidate, label: "가".repeat(100) });
    expect(row.checked).toBe(true);
    expect(isSelectable(row)).toBe(true);
  });
});
