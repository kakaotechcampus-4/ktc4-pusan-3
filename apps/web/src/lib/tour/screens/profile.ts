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
      title: "키·몸무게",
      body: "가장 최근에 잰 것만 보여요. 눌러서 지난 기록과 변화를 볼 수 있어요.",
    },
    {
      // 실서버 빌드에도 서게 된 구역이다 (#713). 🚨 여기서 등록하지 않는다 — 대화가 만든 초안을 확인해 넣는다.
      target: "medications",
      title: "복약",
      body: "대화에 약 이름과 먹일 때를 적으면 복약 일정을 준비해요. 확인하고 넣은 약만 여기에 보이고, 먹인 회차는 눌러서 체크해요.",
    },
    {
      target: "safety",
      title: "알레르기·건강",
      // 괜찮았던 식품 줄은 있을 때만 서서 단계를 따로 두지 않고 여기서 말한다 (#825).
      body: "보호자가 직접 확인한 것만 저장돼요. AI는 이 목록을 만들지도 고치지도 못해요. 식사를 제안할 때 이 목록에 있는 것을 먼저 빼요. 먹어 봤고 괜찮았다고 답한 식품은 목록 위에 따로 모여요.",
    },
    {
      target: "safety-add",
      title: "더하기",
      body: "확인된 알레르기나 지병을 적어 두세요. 등록하기 전에 한 번 더 확인을 받아요.",
    },
  ],
};
