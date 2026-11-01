import type { TourDefinition } from "../types";

/** 05 제안 후보. 🚨 제안이 다 선 뒤에만 뜬다 (준비 중 · 실패만 있는 화면에서는 안 뜬다). */
export const suggestionsTour: TourDefinition = {
  key: "suggestions",
  steps: [
    {
      target: "tabs",
      title: "영역별로 나눠 보여요",
      body: "부탁한 영역마다 따로 준비해요. 위 탭으로 오가며 보세요.",
    },
    {
      target: "row",
      title: "고르기",
      body: "마음에 드는 것을 눌러 골라요. 여러 개를 골라도 돼요. 알레르기 목록에 걸리는 메뉴는 처음부터 빼요.",
    },
    {
      target: "evidence",
      title: "사용한 기록",
      body: "이 제안이 근거로 쓴 기록이에요. 왜 이걸 골랐는지 여기서 확인할 수 있어요.",
    },
    {
      target: "general",
      title: "또래 기준 일반 추천",
      body: "이번 제안에 직접 쓸 아이 기록이 없어서 또래 기준으로 골랐어요. 우리 아이 기록으로 고른 제안과 헷갈리지 않게 점선으로 나눠 보여요.",
    },
    {
      target: "question",
      title: "질문은 하나만",
      body: "답해 주시면 다음부터 우리 아이 기록으로 골라 드려요. 답하지 않아도 괜찮아요.",
    },
    {
      target: "confirm",
      title: "고르고 정하기",
      body: "고른 것은 기록으로 남아 다음 제안에 반영돼요. 일정으로 만들 때는 한 번 더 확인을 받아요.",
    },
  ],
};
