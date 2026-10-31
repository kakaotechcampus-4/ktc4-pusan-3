import { create } from "zustand";

import type { TourMode } from "@/lib/tour/steps";
import type { TourKey } from "@/lib/tour/types";

/**
 * 지금 떠 있는 화면 투어 (#632). 열려 있는 모달 하나라 클라이언트 상태다 (`apps/web/CLAUDE.md` §3).
 *
 * 헤더의 ? 버튼과 덮개(`ScreenTour`)가 서로 다른 자리에 그려져서 둘을 잇는 데만 쓴다.
 * "봤음" 은 여기가 아니라 `lib/tour/seen.ts` 다 — 서버로 옮겨 갈 값이라 섞지 않는다.
 *
 * - `auto` — 처음 들어와서 저절로 뜬 것. 끝에 "? 로 다시 보기" 단계가 붙는다
 * - `help` — ? 를 눌러 연 것
 */
export type { TourMode } from "@/lib/tour/steps";

interface TourState {
  active: { key: TourKey; mode: TourMode } | null;
  start: (key: TourKey, mode: TourMode) => void;
  stop: () => void;
}

export const useTourStore = create<TourState>((set) => ({
  active: null,
  start: (key, mode) => set({ active: { key, mode } }),
  stop: () => set({ active: null }),
}));
