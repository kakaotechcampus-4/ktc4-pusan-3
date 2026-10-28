"use client";

import { Select } from "@/components/ui/select";
import { reminderOptions, type ReminderChoice } from "@/lib/reminder";

/**
 * 일정 알림 고르기 칸 (#609) — 초안 카드(04 · 05) · 08 사진 시트 · 09 하루 패널이 같이 쓴다.
 *
 * 🚨 **목록은 일정 종류에 따라 갈린다.** 종일 일정에는 "며칠 전 오전 9시" 만, 시간 있는 일정에는
 *    "몇 분 · 몇 시간 · 며칠 전" 만 선다. 다른 쪽 값을 보내면 서버가 422 로 거절한다.
 * 🚨 **이미 지난 값은 목록에서 빠진다** (`lib/reminder.ts`). 고른 값만은 지났어도 남는다 —
 *    그 값을 막는 말은 이 칸이 아니라 부르는 쪽이 한다 (초안 카드는 버튼 위 한 곳).
 * 🚨 **"없음" 이 기본값이다** (#594). 알림은 보호자가 고를 때만 생긴다.
 */
export function ReminderField({
  startsAt,
  allDay,
  value,
  onChange,
  label = "알림",
  hint = "나에게만 울려요.",
}: {
  /** 일정 시작. 날짜를 아직 안 고른 초안이면 `null` 이고, 그때는 지난 값을 거르지 않는다. */
  startsAt: string | null;
  allDay: boolean;
  value: ReminderChoice;
  onChange: (value: ReminderChoice) => void;
  label?: string;
  /**
   * 🚨 **알림은 고른 보호자에게만 울린다** (#594). 바로 아래 게이트 문구가 "함께 보는 보호자에게도
   *    보여요" 라서, 알림까지 같이 가는 줄로 읽히지 않게 기본으로 말한다.
   */
  hint?: string;
}) {
  return (
    <Select
      shape="field"
      label={label}
      hint={hint}
      value={value}
      options={reminderOptions({ startsAt, allDay, keep: value })}
      onChange={onChange}
    />
  );
}
