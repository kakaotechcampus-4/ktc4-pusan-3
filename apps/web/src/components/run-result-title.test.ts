import { describe, expect, it } from "vitest";

import { refusedToSave, resultTitle } from "@/components/run-result";

/**
 * 04 의 제목 (#868). 🚨 "이 말은 기록으로 남기지 않았어요" 는 저장을 대신 거절한 안내에만 선다 —
 *    추천 2개가 선 run 에 실리는 `agent_limit` 같은 안내까지 거절로 읽으면 답을 받은 보호자가 거절된 줄 안다.
 */
const base = {
  savedSomething: false,
  interrupted: false,
  askingMore: false,
  noticedWhy: true,
  guided: false,
  followUp: false,
};

describe("refusedToSave", () => {
  it("알레르기 · 키몸무게 안내는 저장을 거절한 것이다", () => {
    expect(refusedToSave([{ code: "safety_record" }])).toBe(true);
    expect(refusedToSave([{ code: "growth_record" }])).toBe(true);
  });

  it("🚨 한 번에 두 가지까지 · 응급 · 진단 안내는 거절이 아니다", () => {
    expect(refusedToSave([{ code: "agent_limit" }, { code: "task_limit" }])).toBe(false);
    expect(refusedToSave([{ code: "emergency" }])).toBe(false);
    expect(refusedToSave([{ code: "diagnosis" }])).toBe(false);
    expect(refusedToSave([])).toBe(false);
  });
});

describe("resultTitle", () => {
  it("저장을 거절한 안내면 저장 0건을 제목이 말한다", () => {
    expect(resultTitle({ ...base, guided: true })).toBe("이 말은 기록으로 남기지 않았어요");
  });

  it("🚨 거절이 아닌 안내와 추천 답만 온 run 은 확인했다고 말한다", () => {
    expect(resultTitle(base)).toBe("적어 주신 말을 확인했어요");
  });

  it("저장한 것이 있으면 안내가 있어도 저장했다고 말한다", () => {
    expect(resultTitle({ ...base, savedSomething: true, guided: true })).toBe("이렇게 저장했어요");
  });
});
