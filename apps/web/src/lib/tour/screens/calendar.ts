import type { TourDefinition } from "../types";

/** 09 캘린더. 고른 날(처음엔 오늘)에 없는 구역은 단계에서 빠진다. */
export const calendarTour: TourDefinition = {
  key: "calendar",
  steps: [
    {
      target: "grid",
      title: "날짜 고르기",
      body: "일정이나 기록, 일기가 있는 날에 표시가 붙어요. 날짜를 누르면 그날 있었던 일이 아래에 모여요.",
    },
    {
      target: "diary",
      title: "이날의 일기",
      body: "그날 어땠는지 편하게 남겨요. 쓰지 않은 날은 비워 두고, 채우려고 지어내지 않아요.",
    },
    {
      target: "events",
      title: "저장된 일정",
      body: "확인하고 넣은 일정만 여기에 있어요. 대화에서 찾은 일정은 넣기 전에는 들어오지 않아요.",
    },
    {
      target: "prep",
      title: "준비물",
      body: "챙긴 것은 눌러서 표시해요.",
    },
    {
      target: "records",
      title: "이날 남긴 기록",
      body: "그날 적은 말에서 나온 기록이에요. 누르면 자세히 보고 고칠 수 있어요.",
    },
    {
      target: "chat",
      title: "이날 나눈 대화",
      body: "그날 주고받은 대화로 가요.",
    },
  ],
};
