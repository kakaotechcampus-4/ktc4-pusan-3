import { HELP_STEP, type TourDefinition, type TourStep } from "./types";

/**
 * 투어를 열 때 어느 단계를 보여 줄지 고른다 (`docs/web/screen-tour-v1.md` §3).
 * DOM 을 모른다 — "그 요소가 지금 그려져 있나" 는 부르는 쪽(`components/screen-tour.tsx`)이 `drawn` 으로 준다.
 */

export type TourMode = "auto" | "help";

/** 이 화면의 단계 중 지금 그려진 것. ? 안내 단계는 세지 않는다. */
export function contentSteps(
  tour: TourDefinition,
  seen: ReadonlySet<string> | undefined,
  drawn: (step: TourStep) => boolean,
): TourStep[] {
  return tour.steps.filter((s) => !(s.skipIfSeen && seen?.has(s.skipIfSeen)) && drawn(s));
}

/**
 * 지금 화면에 있는 단계만. 열 때 한 번 고른다 — 단계마다 분모가 바뀌면 "2 / 5" 가 거짓말이 된다.
 * 🚨 **설명할 것이 하나도 없으면 ? 안내 한 단계만 띄우지 않는다** — 막힘 안내만 선 05 처럼 비출 것이 없는
 *    화면에서 "다시 보고 싶을 때" 하나가 뜨면 무엇을 다시 본다는 건지 알 수 없다 (실서버에서 그랬다).
 * 🚨 ? 안내 단계는 **자동으로 뜬 투어에만** 붙는다. ? 를 눌러 연 사람은 이미 그 버튼을 안다.
 */
export function liveSteps(
  tour: TourDefinition,
  mode: TourMode,
  seen: ReadonlySet<string> | undefined,
  drawn: (step: TourStep) => boolean,
): TourStep[] {
  const steps = contentSteps(tour, seen, drawn);
  if (steps.length === 0) return [];
  return mode === "auto" && drawn(HELP_STEP) ? [...steps, HELP_STEP] : steps;
}
