import { describe, expect, it } from "vitest";

import { itemNames } from "@/components/suggestion-items";

/**
 * 필요한 재료 · 준비물에 무엇을 그리는가 (#659). 목 픽스처는 health 추천이 없고 `items` 를 늘 배열로 싣는다 —
 * 실서버가 칸을 내리기 전의 `undefined` 와 health 경로는 화면 테스트가 지나가지 않아서 여기서 건다.
 */
describe("itemNames", () => {
  it("health 는 권하는 것이 없어서 항목이 와도 그리지 않는다", () => {
    expect(itemNames("health", ["체온계"])).toEqual([]);
  });

  it("칸이 빠져 오면(서버가 내리기 전) 빈 배열과 같다", () => {
    expect(itemNames("food", undefined)).toEqual([]);
  });

  it("공백만 있는 항목은 거르고, 나머지는 서버 순서 그대로 둔다 — 합치지 않는다", () => {
    expect(itemNames("activity", [" 돗자리", "", "  ", "물통", "물통"])).toEqual([
      "돗자리",
      "물통",
      "물통",
    ]);
  });

  it("식사 · 놀이 · 교육은 그린다", () => {
    for (const agent of ["food", "activity", "growth"] as const) {
      expect(itemNames(agent, ["가"])).toEqual(["가"]);
    }
  });

  it("도서처럼 항목이 제안 문장과 같은 하나뿐이면 그릴 것이 없다 (#892)", () => {
    expect(itemNames("growth", ["책 제목(지은이)"], "책 제목(지은이)")).toEqual([]);
  });

  it("제안 문장과 같은 항목만 빼고 나머지는 그대로 둔다 — 앞뒤 공백은 무시한다", () => {
    expect(itemNames("activity", ["블록 ", "바구니"], " 블록")).toEqual(["바구니"]);
  });

  it("항목이 제안 문장 안에 들어 있어도 같은 글이 아니면 그린다", () => {
    expect(itemNames("food", ["두부"], "두부 조림")).toEqual(["두부"]);
  });
});
