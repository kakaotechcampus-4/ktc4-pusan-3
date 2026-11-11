import type { TourDefinition } from "../types";

/**
 * 04 대화. 🚨 결과가 다 온 뒤에만 뜬다 — 진행 표시가 도는 동안 덮으면 결과가 오는 순간을 가린다.
 * 대화가 여러 턴이면 **가장 최근 턴 하나** 안에서만 짚는다 (`within: "turn"`). 그 턴에 없는 단계는 빠진다.
 * 🚨 6단계 이하 (`docs/web/screen-tour-v1.md` §3). "기억이 자랐어요" 와 "날짜별로 남아요" 는 뺐다 (#815) —
 *    기억은 03 홈의 "눈여겨볼 것" 과 07 이 설명하고, 지난 대화는 위로 올리면 바로 보인다.
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
      target: "found-tabs",
      within: "turn",
      title: "찾은 기록과 일정",
      body: "물어본 기록과 일정을 종류별로 보여 드려요. 여기서는 보기만 하고 아무것도 바꾸지 않아요.",
    },
    {
      target: "delete-proposal",
      within: "turn",
      title: "지울 것 고르기",
      body: "말만으로는 지워지지 않아요. 체크한 것만, 삭제 버튼을 두 번 눌러야 지워져요. 지운 기록은 되살릴 수 없어요.",
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
  ],
};
