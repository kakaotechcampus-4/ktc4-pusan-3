import { describe, expect, it } from "vitest";

import { parentDisplayName, RELATION_LABEL } from "./relation";

/**
 * 함께 보는 보호자를 부르는 이름 (#336 · #351). 서버는 이름이 비어 있으면 `nickname: null` 을
 * 내리고, 화면은 이 이름 뒤에 조사를 붙인다("현우가 더는 못 보게 할까요?").
 */
describe("parentDisplayName", () => {
  it("이름이 있으면 이름이다", () => {
    expect(parentDisplayName({ nickname: "현우", relation: "father" })).toBe("현우");
  });

  it("이름이 비면 관계 이름이다", () => {
    expect(parentDisplayName({ nickname: null, relation: "sitter" })).toBe(RELATION_LABEL.sitter);
    expect(parentDisplayName({ nickname: "  ", relation: "mother" })).toBe(RELATION_LABEL.mother);
  });

  it('관계가 other 면 "그 밖에" 가 아니라 "함께 보는 보호자" 다 — 사람을 부르는 말이어야 한다', () => {
    expect(parentDisplayName({ nickname: null, relation: "other" })).toBe("함께 보는 보호자");
  });
});
