import type { TourDefinition } from "../types";

/**
 * 03 홈. 🚨 둘로 나눈다 (`docs/web/screen-tour-v1.md` §2) — 가입 직후 기록 0건인 홈에는 빈 상태 단계만,
 * 첫 기록이 생긴 뒤 다시 왔을 때 나머지를 보여 준다. 초대받아 기록이 이미 있는 홈에 온 보호자는
 * `home` 만 보는데, 입력창 단계는 `home-empty` 를 안 봤으니 그대로 남는다 (`skipIfSeen`).
 */

const COMPOSER = {
  target: "composer",
  title: "여기에 한 줄 적어 주세요",
  body: "배우자에게 카톡 보내듯 적으면 돼요. 한 줄에 여러 일이 섞여 있어도 하나씩 나눠서 기록해요.",
} as const;

/** 🚨 사진 버튼은 서버에 기능이 생겨야 선다 (`lib/features.ts`). 없으면 이 단계도 빠진다. */
const PHOTO = {
  target: "photo",
  title: "사진으로도 적을 수 있어요",
  body: "알림장이나 식단표를 찍으면 적힌 내용을 읽어 와요. 확인을 누르기 전에는 저장하지 않아요.",
} as const;

const PROMPTS = {
  target: "prompts",
  title: "제안 받기",
  body: "누르면 지금까지의 기록을 보고 준비해요. 기록이 부족하면 또래 기준이라고 따로 표시해요.",
} as const;

export const homeEmptyTour: TourDefinition = {
  key: "home-empty",
  steps: [
    COMPOSER,
    PHOTO,
    {
      target: "empty",
      title: "아직 기록이 없어요",
      body: "기록이 쌓이면 여기에 이번 주 기록과 눈여겨볼 것이 모여요. 오늘 있었던 일 하나부터 적어 보세요.",
    },
    PROMPTS,
  ],
};

export const homeTour: TourDefinition = {
  key: "home",
  steps: [
    { ...COMPOSER, skipIfSeen: "home-empty" },
    { ...PHOTO, skipIfSeen: "home-empty" },
    {
      target: "today",
      title: "오늘 챙길 것",
      body: "오늘 급식과 일정이 여기에 모여요. 일정은 확인하고 넣은 것만 보여요.",
    },
    {
      target: "highlight",
      title: "눈여겨볼 것",
      body: "서로 다른 날에 반복된 것만 여기에 올라와요. 한 번 있었던 일로 아이의 성향을 정하지 않아요.",
    },
    PROMPTS,
    {
      target: "chat-entry",
      title: "오늘 대화 이어보기",
      body: "오늘 주고받은 대화로 돌아가요. 답을 기다리는 질문이 있으면 여기서 알려 드려요.",
    },
  ],
};
