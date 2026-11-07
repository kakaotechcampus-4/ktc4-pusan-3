import type { TourDefinition } from "../types";

/** 10 설정. 🚨 저절로 뜨지 않는다 — 홈에서 들어가는 곁가지라 ? 로만 연다 (`ScreenTour` 의 `auto`). */
export const settingsTour: TourDefinition = {
  key: "settings",
  auto: false,
  steps: [
    {
      target: "parents",
      title: "함께 보는 보호자",
      body: "같은 아이의 기록을 함께 보고 적는 사람들이에요. 아이를 등록한 보호자는 더하기를 눌러 초대할 수 있어요.",
    },
    {
      target: "consents",
      title: "동의",
      body: "지금 켜고 끌 수 있는 동의예요. 바꾸기 전에 무엇이 달라지는지 먼저 보여 드려요.",
    },
    {
      target: "support",
      title: "도움이 필요할 때",
      body: "자주 묻는 질문을 먼저 찾아보고, AI 답이 이상하거나 앱이 멈추면 문제 알리기로 알려 주세요.",
    },
    {
      target: "replay",
      title: "화면 설명 다시 보기",
      body: "모든 화면의 처음 설명을 다시 켜요. 다음에 각 화면에 들어가면 다시 보여 드려요.",
    },
  ],
};
