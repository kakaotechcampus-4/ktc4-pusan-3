import type { TourDefinition } from "../types";

/**
 * 04 대화. 🚨 결과가 다 온 뒤에만 뜬다 — 진행 표시가 도는 동안 덮으면 결과가 오는 순간을 가린다.
 * 대화가 여러 턴이면 **가장 최근 턴 하나** 안에서만 짚는다 (`within: "turn"`). 그 턴에 없는 단계는 빠진다.
 */
export const chatTour: TourDefinition = {
  key: "chat",
  steps: [
    {
      target: "bubble",
      within: "turn",
      title: "내가 적은 말",
      body: "보낸 말은 그대로 남아요. 이 말에서 아이에 관한 것만 골라 기록해요.",
    },
    {
      target: "saved",
      within: "turn",
      title: "저장한 기록",
      body: "무엇을 기록으로 남겼는지 보여 드려요. 잘못 알아들었으면 기록과 기억 화면에서 고칠 수 있어요.",
    },
    {
      target: "promoted",
      within: "turn",
      title: "기억이 자랐어요",
      body: "같은 일이 서로 다른 날에 반복되면 기억이 돼요. 한 번 적은 것으로 바로 정하지 않아요.",
    },
    {
      target: "drafts",
      within: "turn",
      title: "일정으로 만들까요?",
      body: "말 속에서 일정을 찾으면 초안만 만들어요. 확인하고 넣기를 눌러야 캘린더에 들어가요.",
    },
    {
      target: "question",
      within: "turn",
      title: "한 가지만 더",
      body: "더 알아야 할 게 있으면 하나만 여쭤봐요. 아래 입력창에 답하면 이어서 반영해요.",
    },
    {
      target: "day",
      title: "날짜별로 남아요",
      body: "주고받은 대화는 날짜별로 쌓여요. 위로 올리면 지난 날의 대화가 이어서 보여요.",
    },
  ],
};
