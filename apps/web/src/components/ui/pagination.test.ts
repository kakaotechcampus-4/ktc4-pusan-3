import { describe, expect, it } from "vitest";

import { pageWindow } from "./pagination";

describe("pageWindow (#584)", () => {
  it("지금 쪽을 가운데 둔 다섯 개다", () => {
    expect(pageWindow(6, 12)).toEqual([4, 5, 6, 7, 8]);
  });

  it("처음에 닿으면 창이 처음에 붙는다", () => {
    expect(pageWindow(1, 12)).toEqual([1, 2, 3, 4, 5]);
    expect(pageWindow(2, 12)).toEqual([1, 2, 3, 4, 5]);
  });

  it("끝에 닿으면 창이 끝에 붙는다", () => {
    expect(pageWindow(12, 12)).toEqual([8, 9, 10, 11, 12]);
    expect(pageWindow(11, 12)).toEqual([8, 9, 10, 11, 12]);
  });

  it("쪽이 다섯보다 적으면 있는 만큼만이다", () => {
    expect(pageWindow(2, 3)).toEqual([1, 2, 3]);
  });
});
