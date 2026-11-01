import { describe, expect, it } from "vitest";

import { homeTour } from "./screens/home";
import { liveSteps } from "./steps";
import { HELP_STEP, type TourDefinition, type TourStep } from "./types";

/** 화면에 그려진 `data-tour` 를 흉내 낸다. */
function drawn(...targets: string[]) {
  return (step: TourStep) => targets.includes(step.target);
}

const tour: TourDefinition = {
  key: "memories",
  steps: [
    { target: "tabs", title: "탭", body: "" },
    { target: "row", title: "줄", body: "" },
  ],
};

describe("liveSteps — 열 때 보여 줄 단계 (docs/web/screen-tour-v1.md §3)", () => {
  it("그려지지 않은 요소의 단계는 빠진다", () => {
    expect(liveSteps(tour, "help", undefined, drawn("tabs")).map((s) => s.target)).toEqual([
      "tabs",
    ]);
  });

  it("자동으로 뜬 투어에만 ? 안내 단계가 끝에 붙는다", () => {
    const has = drawn("tabs", "row", HELP_STEP.target);
    expect(liveSteps(tour, "auto", undefined, has).at(-1)).toBe(HELP_STEP);
    expect(liveSteps(tour, "help", undefined, has)).not.toContain(HELP_STEP);
  });

  /** 🚨 실서버 05 에서 식사가 막히고 제안이 없을 때 "다시 보고 싶을 때" 하나만 떴다. */
  it("설명할 단계가 하나도 없으면 ? 안내 단계만 띄우지 않는다", () => {
    expect(liveSteps(tour, "auto", undefined, drawn(HELP_STEP.target))).toEqual([]);
  });

  it("home-empty 를 봤으면 home 에서 입력창 단계를 다시 꺼내지 않는다", () => {
    const has = drawn("composer", "highlight");
    expect(liveSteps(homeTour, "help", undefined, has).map((s) => s.target)).toEqual([
      "composer",
      "highlight",
    ]);
    expect(liveSteps(homeTour, "help", new Set(["home-empty"]), has).map((s) => s.target)).toEqual([
      "highlight",
    ]);
  });
});
