import { describe, expect, it } from "vitest";

import { toRow } from "@/components/safety-scan-fields";
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
