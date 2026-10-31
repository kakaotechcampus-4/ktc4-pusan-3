import type { ReminderLead } from "@/lib/api/types";

import { seoulDate } from "./event-draft";

/**
 * 일정 알림 — 고를 수 있는 값과 **고를 수 없는 값을 가려내는 것** (#594 · #609).
 *
 * 🚨 **정본은 서버다** (`apps/api/app/rules/reminder.py`). 서버가 같은 계산으로 받을지 말지를
 *    정하고, 맞지 않거나 지난 값은 `422 invalid_reminder` 로 거절한다. 여기 있는 것은 그 422 를
 *    보호자가 누르기 전에 피하게 하는 **입력 제약**이다 — 표시가 아니다 (apps/web/CLAUDE.md §4
 *    "날짜를 프론트에서 계산하지 않는다" 의 예외, `DateField` 의 `toDate` 와 같은 자리).
 *    울릴 시각을 화면에 적지 않는 것도 그래서다. 화면은 "1시간 전" 처럼 고른 값만 말한다.
 *
 * 🚨 **목록이 두 벌이다.** 시간 있는 일정은 시작에서 거꾸로 센 간격이고, 종일 일정은 시작이
 *    자정이라 "몇 분 전" 이 뜻이 없어서 "며칠 전 오전 9시" (KST) 로 센다.
 */

/** "없음" — 계약의 값이 아니라 고르기 칸의 자리다. 보낼 때는 `null` 이다. */
export const NO_REMINDER = "none" as const;
export type ReminderChoice = ReminderLead | typeof NO_REMINDER;

const MINUTE = 60_000;
const DAY = 24 * 60 * MINUTE;

/** 시간 있는 일정 — 시작에서 얼마나 먼저인가. 서버 `_TIMED` 와 같은 순서 · 같은 값. */
const TIMED: ReadonlyArray<{ lead: ReminderLead; before: number; label: string }> = [
  { lead: "at_start", before: 0, label: "시작할 때" },
  { lead: "5m", before: 5 * MINUTE, label: "5분 전" },
  { lead: "10m", before: 10 * MINUTE, label: "10분 전" },
  { lead: "15m", before: 15 * MINUTE, label: "15분 전" },
  { lead: "30m", before: 30 * MINUTE, label: "30분 전" },
  { lead: "1h", before: 60 * MINUTE, label: "1시간 전" },
  { lead: "2h", before: 120 * MINUTE, label: "2시간 전" },
  { lead: "1d", before: DAY, label: "1일 전" },
  { lead: "2d", before: 2 * DAY, label: "2일 전" },
  { lead: "1w", before: 7 * DAY, label: "1주 전" },
];

/** 종일 일정 — 며칠 전 오전 9시인가. 서버 `_ALL_DAY` 와 같은 값. */
const ALL_DAY: ReadonlyArray<{ lead: ReminderLead; days: number; label: string }> = [
  { lead: "day_of_9am", days: 0, label: "당일 오전 9시" },
  { lead: "1d_9am", days: 1, label: "1일 전 오전 9시" },
  { lead: "2d_9am", days: 2, label: "2일 전 오전 9시" },
  { lead: "1w_9am", days: 7, label: "1주 전 오전 9시" },
];

/**
 * 일정 종류가 바뀔 때 고른 값을 옮기는 표. 서버 `for_kind` 와 같은 방향이다 (#603 리뷰).
 * 🚨 지우지 않는다 — 예방접종 · 병원 일정은 알림이 안 오는 쪽이 조금 다른 시각에 오는 것보다 나쁘다.
 */
const TO_ALL_DAY: Partial<Record<ReminderLead, ReminderLead>> = {
  at_start: "day_of_9am",
  "5m": "day_of_9am",
  "10m": "day_of_9am",
  "15m": "day_of_9am",
  "30m": "day_of_9am",
  "1h": "day_of_9am",
  "2h": "day_of_9am",
  "1d": "1d_9am",
  "2d": "2d_9am",
  "1w": "1w_9am",
};
const TO_TIMED: Partial<Record<ReminderLead, ReminderLead>> = {
  day_of_9am: "at_start",
  "1d_9am": "1d",
  "2d_9am": "2d",
  "1w_9am": "1w",
};

export function reminderLabel(choice: ReminderChoice): string {
  if (choice === NO_REMINDER) return "없음";
  return (
    TIMED.find((each) => each.lead === choice)?.label ??
    ALL_DAY.find((each) => each.lead === choice)?.label ??
    choice
  );
}

/** 이 값을 그 종류의 일정에 걸 수 있는가. 서버 `fits`. */
export function fitsKind(lead: ReminderLead, allDay: boolean): boolean {
  return (allDay ? ALL_DAY : TIMED).some((each) => each.lead === lead);
}

/** 그 종류에 맞는 값. 이미 맞으면 그대로, 아니면 날 수를 지켜 가까운 값으로. 서버 `for_kind`. */
export function forKind(choice: ReminderChoice, allDay: boolean): ReminderChoice {
  if (choice === NO_REMINDER || fitsKind(choice, allDay)) return choice;
  return (allDay ? TO_ALL_DAY : TO_TIMED)[choice] ?? NO_REMINDER;
}

/**
 * 울릴 시각(ms). 종류가 맞지 않거나 시작을 읽을 수 없으면 `null`. 서버 `remind_at`.
 *
 * 🚨 **종일 일정의 날짜는 서울 기준으로 읽는다.** 종일 일정은 그날 KST 자정에 시작해서, UTC 로
 *    날짜를 읽으면 전날이 돼 하루 일찍 울린다. 서울은 서머타임이 없어 `+09:00` 고정이 맞다.
 */
export function remindAt(lead: ReminderLead, startsAt: string, allDay: boolean): number | null {
  if (allDay) {
    const days = ALL_DAY.find((each) => each.lead === lead)?.days;
    const date = seoulDate(startsAt);
    if (days === undefined || date === "") return null;
    return Date.parse(`${date}T09:00:00+09:00`) - days * DAY;
  }
  const before = TIMED.find((each) => each.lead === lead)?.before;
  const start = Date.parse(startsAt);
  if (before === undefined || Number.isNaN(start)) return null;
  return start - before;
}

/**
 * 지금 고를 수 있는 값 — "없음" 이 맨 앞이고, 울릴 시각이 이미 지난 값은 **목록에서 뺀다.**
 *
 * 🚨 **비활성으로 두지 않고 뺀다.** 눌리지 않는 항목은 "왜 안 눌리지" 를 먼저 묻게 한다
 *    (`event-draft-card.tsx` 의 "하루 종일이면 칸을 감춘다" 와 같은 판단).
 * 🚨 **시작을 아직 모르면 거르지 않는다** (날짜를 안 고른 초안). 고를 날이 정해지면 다시 거른다.
 * 🚨 **이미 고른 값은 지났어도 남긴다** (`keep`). 빼면 고르기 칸이 "안 고름" 으로 보여서, 고른 적
 *    없는 것처럼 읽히는데 실제로는 그 값이 보내진다. 남겨 두고 막는 것은 부르는 쪽이 한다.
 */
export function reminderOptions({
  startsAt,
  allDay,
  now = Date.now(),
  keep,
}: {
  startsAt: string | null;
  allDay: boolean;
  now?: number;
  keep?: ReminderChoice;
}): Array<{ value: ReminderChoice; label: string }> {
  const leads = (allDay ? ALL_DAY : TIMED)
    .map((each) => each.lead)
    .filter((lead) => lead === keep || startsAt === null || !isPast(lead, startsAt, allDay, now));
  const choices: ReminderChoice[] = [NO_REMINDER, ...leads];
  return choices.map((value) => ({ value, label: reminderLabel(value) }));
}

/** 울릴 시각이 이미 지났는가. 서버는 같은 시각도 지난 것으로 본다 (`<=`). */
export function isPast(lead: ReminderLead, startsAt: string, allDay: boolean, now = Date.now()) {
  const at = remindAt(lead, startsAt, allDay);
  return at !== null && at <= now;
}
