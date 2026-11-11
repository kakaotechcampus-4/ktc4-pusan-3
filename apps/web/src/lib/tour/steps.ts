import { HELP_STEP, type TourDefinition, type TourKey, type TourStep } from "./types";

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

/**
 * "봤음" 캐시에 한 화면을 얹는다 (`lib/tour/seen.ts` 의 `useMarkTourSeen` · #815 리뷰).
 *
 * 🚨 **읽은 목록이 없으면(`undefined`) 만들지 않는다.** `GET /me/tours` 가 실패해 목록이 없을 때 ? 로 본
 *    투어 하나만 든 목록을 만들면, 캐시가 "성공" 이 되어 서버에는 본 것으로 남은 다른 화면 투어가 그 세션
 *    내내 저절로 뜬다 — "읽기가 실패하면 자동으로 띄우지 않는다" 가 깨진다 (`docs/web/screen-tour-v1.md` §2).
 */
export function withSeen(old: TourKey[] | undefined, key: TourKey): TourKey[] | undefined {
  if (old === undefined) return undefined;
  return old.includes(key) ? old : [...old, key];
}
