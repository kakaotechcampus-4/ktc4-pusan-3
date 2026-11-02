import type { TourDefinition } from "../types";

/** 07 기록/기억. 🚨 기록이 1건 이상일 때만 저절로 뜬다 — 빈 목록을 설명하게 된다. */
export const memoriesTour: TourDefinition = {
  key: "memories",
  steps: [
    {
      target: "tabs",
      title: "기록과 기억",
      body: "기록은 적어 주신 말에서 나온 한 건이고, 기억은 기록이 서로 다른 날 반복돼서 생긴 거예요. 제안 피드백에서는 고른 제안이 어땠는지 남길 수 있어요.",
    },
    {
      target: "search",
      title: "적은 말로 찾기",
      body: "그때 적었던 말로 찾아요.",
    },
    {
      target: "filter",
      title: "골라 보기",
      body: "분류나 기간으로 좁혀요. 고친 기록만 따로 볼 수도 있어요.",
    },
    {
      target: "row",
      title: "눌러서 고치기",
      body: "이번만 그랬어요를 고르면 기억으로 세지 않고, 잘못된 기록을 고르면 제안 근거에서도 빠져요. 고르기 전에 무엇이 바뀌는지 먼저 보여 드려요.",
    },
  ],
};
