import { describe, expect, it } from "vitest";

import type { ReminderLead } from "@/lib/api/types";
import { fitsKind, forKind, isPast, NO_REMINDER, remindAt, reminderOptions } from "@/lib/reminder";

/**
 * 🚨 **서버 `app/rules/reminder.py` 와 같은 답을 내는지**를 건다 (#609). 화면은 이 계산으로 지난 값을
 *    목록에서 빼는데, 서버와 어긋나면 고른 값이 422 로 거절되거나(일정까지 못 넣는다) 고를 수 있는
 *    값이 사라진다. 서버 쪽 같은 검사는 `apps/api/tests/unit/rules/test_reminder.py` 다.
 */
const TIMED: ReminderLead[] = ["at_start", "5m", "10m", "15m", "30m", "1h", "2h", "1d", "2d", "1w"];
const ALL_DAY: ReminderLead[] = ["day_of_9am", "1d_9am", "2d_9am", "1w_9am"];

describe("울릴 시각", () => {
  it("시간 있는 일정은 시작에서 거꾸로 센다", () => {
    const start = "2026-11-10T15:00:00+09:00";
    expect(remindAt("at_start", start, false)).toBe(Date.parse(start));
    expect(remindAt("10m", start, false)).toBe(Date.parse("2026-11-10T14:50:00+09:00"));
    expect(remindAt("1h", start, false)).toBe(Date.parse("2026-11-10T14:00:00+09:00"));
    expect(remindAt("2d", start, false)).toBe(Date.parse("2026-11-08T15:00:00+09:00"));
    expect(remindAt("1w", start, false)).toBe(Date.parse("2026-11-03T15:00:00+09:00"));
  });

  it("종일 일정은 며칠 전 KST 오전 9시다", () => {
    const start = "2026-11-10T00:00:00+09:00";
    expect(remindAt("day_of_9am", start, true)).toBe(Date.parse("2026-11-10T09:00:00+09:00"));
    expect(remindAt("1d_9am", start, true)).toBe(Date.parse("2026-11-09T09:00:00+09:00"));
    expect(remindAt("1w_9am", start, true)).toBe(Date.parse("2026-11-03T09:00:00+09:00"));
  });

  it("🚨 종일 일정이 UTC 로 와도 날짜는 서울 기준이다 — 하루 일찍 울리지 않는다", () => {
    // 서버 응답은 KST 자정을 UTC(전날 15:00Z)로 실을 수 있다. UTC 날짜로 읽으면 11-09 가 된다.
    expect(remindAt("day_of_9am", "2026-11-09T15:00:00Z", true)).toBe(
      Date.parse("2026-11-10T09:00:00+09:00"),
    );
  });

  it("종류가 맞지 않는 값은 시각이 없다", () => {
    expect(remindAt("1h", "2026-11-10T00:00:00+09:00", true)).toBeNull();
    expect(remindAt("day_of_9am", "2026-11-10T15:00:00+09:00", false)).toBeNull();
  });

  it("🚨 서버처럼 같은 시각도 지난 것으로 본다", () => {
    const start = "2026-11-10T15:00:00+09:00";
    const at = Date.parse("2026-11-10T14:00:00+09:00");
    expect(isPast("1h", start, false, at)).toBe(true);
    expect(isPast("1h", start, false, at - 1)).toBe(false);
  });
});

describe("일정 종류", () => {
  it("모든 값은 한 종류에만 속한다", () => {
    for (const lead of TIMED)
      expect([fitsKind(lead, false), fitsKind(lead, true)]).toEqual([true, false]);
    for (const lead of ALL_DAY)
      expect([fitsKind(lead, false), fitsKind(lead, true)]).toEqual([false, true]);
  });

  it("🚨 종류를 바꾸면 지우지 않고 날 수를 지켜 옮긴다 — 서버 for_kind 와 같은 표", () => {
    expect(TIMED.map((lead) => forKind(lead, true))).toEqual([
      ...Array<ReminderLead>(7).fill("day_of_9am"),
      "1d_9am",
      "2d_9am",
      "1w_9am",
    ]);
    expect(ALL_DAY.map((lead) => forKind(lead, false))).toEqual(["at_start", "1d", "2d", "1w"]);
  });

  it("이미 맞는 값과 '없음' 은 그대로다", () => {
    expect(forKind("1h", false)).toBe("1h");
    expect(forKind("1d_9am", true)).toBe("1d_9am");
    expect(forKind(NO_REMINDER, true)).toBe(NO_REMINDER);
  });
});

describe("고를 수 있는 값", () => {
  const values = (options: ReturnType<typeof reminderOptions>) => options.map((o) => o.value);

  it("'없음' 이 맨 앞이고, 종류에 맞는 목록만 선다", () => {
    const now = Date.parse("2026-10-01T00:00:00+09:00");
    expect(
      values(reminderOptions({ startsAt: "2026-11-10T15:00:00+09:00", allDay: false, now })),
    ).toEqual([NO_REMINDER, ...TIMED]);
    expect(
      values(reminderOptions({ startsAt: "2026-11-10T00:00:00+09:00", allDay: true, now })),
    ).toEqual([NO_REMINDER, ...ALL_DAY]);
  });

  it("🚨 울릴 시각이 지난 값은 목록에서 빠진다", () => {
    // 시작 90분 전 — 1시간 전까지만 남고 2시간 전 · 며칠 전은 이미 지났다.
    const now = Date.parse("2026-11-10T13:30:00+09:00");
    expect(
      values(reminderOptions({ startsAt: "2026-11-10T15:00:00+09:00", allDay: false, now })),
    ).toEqual([NO_REMINDER, "at_start", "5m", "10m", "15m", "30m", "1h"]);
  });

  it("지난 일정에는 '없음' 하나만 남는다", () => {
    const now = Date.parse("2026-11-11T00:00:00+09:00");
    expect(
      values(reminderOptions({ startsAt: "2026-11-10T15:00:00+09:00", allDay: false, now })),
    ).toEqual([NO_REMINDER]);
  });

  it("🚨 이미 고른 값은 지났어도 남긴다 — 빼면 고른 적 없는 것처럼 보인다", () => {
    const now = Date.parse("2026-11-10T13:30:00+09:00");
    const options = reminderOptions({
      startsAt: "2026-11-10T15:00:00+09:00",
      allDay: false,
      now,
      keep: "1d",
    });
    expect(values(options)).toContain("1d");
    expect(values(options)).not.toContain("2d");
  });

  it("날짜를 아직 안 골랐으면 거르지 않는다", () => {
    const now = Date.parse("2030-01-01T00:00:00+09:00");
    expect(values(reminderOptions({ startsAt: null, allDay: false, now }))).toEqual([
      NO_REMINDER,
      ...TIMED,
    ]);
  });
});
