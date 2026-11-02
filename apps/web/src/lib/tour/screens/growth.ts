import type { TourDefinition } from "../types";

/**
 * 11-1 키 · 몸무게. 🚨 평가하는 말을 쓰지 않는다 — "잘 크고 있어요", "또래보다", "정상" 같은 말은
 * 투어에도 넣지 않는다 (최상위 CLAUDE.md §2 개인정보 · 디자인 시스템 §7 변화 그래프).
 */
export const growthTour: TourDefinition = {
  key: "growth",
  steps: [
    {
      target: "chart",
      title: "변화",
      body: "적어 둔 값을 잰 날짜순으로 이은 선이에요. 또래 평균이나 백분위와 비교하지 않아요.",
    },
    {
      target: "list",
      title: "적어 둔 기록",
      body: "그래프는 이 목록을 그린 거예요. 잘못 적은 값은 연필로 고치고, 필요 없으면 지워요.",
    },
    {
      target: "add",
      title: "새로 적기",
      body: "어린이집 신체검사나 병원에서 잰 날 적어 두면 돼요. 키나 몸무게 하나만 적어도 괜찮아요.",
    },
  ],
};
