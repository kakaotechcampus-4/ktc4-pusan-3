import type { TourDefinition } from "../types";

/** 11 아이 프로필. 🚨 알레르기 단계는 화면에 이미 있는 문장을 그대로 쓴다 (최상위 CLAUDE.md §2 안전). */
export const profileTour: TourDefinition = {
  key: "profile",
  steps: [
    {
      target: "identity",
      title: "기본 정보",
      body: "별명, 생일, 성별이에요. 나이는 생일을 보고 계산해요. 연필을 눌러 고칠 수 있어요.",
    },
    {
      target: "growth",
      title: "키 · 몸무게",
      body: "가장 최근에 잰 것만 보여요. 눌러서 지난 기록과 변화를 볼 수 있어요.",
    },
    {
      target: "safety",
      title: "알레르기 · 건강",
      body: "보호자가 직접 확인한 것만 저장돼요. AI 는 이 목록을 만들지도 고치지도 못해요. 식사 제안은 이 목록으로 먼저 걸러요.",
    },
    {
      target: "safety-add",
      title: "더하기",
      body: "확인된 알레르기나 지병을 적어 두세요. 등록하기 전에 한 번 더 확인을 받아요.",
    },
  ],
};
