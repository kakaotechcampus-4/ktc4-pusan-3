import { describe, expect, it } from "vitest";

import type { PhotoEntry } from "@/lib/api/types";
import {
  canBecomeEvent,
  carriedEndsAt,
  droppedEnd,
  entryDateLabel,
  entryDateRole,
  eventBody,
  eventWhen,
  initialPlan,
  planLocks,
  readMoment,
  readSpan,
  readStartTime,
  recordedEntry,
  offBecausePast,
  startsChecked,
} from "@/lib/photo-entry";

/**
 * 08 사진 — 항목의 날짜가 무엇인지 (#723). 🚨 서버 `photo_run.py` 의 `_entry_date` · `apply_edits`
 * 와 같은 갈래여야 한다. 어긋나면 화면은 "마감" 이라고 말하는데 서버는 시작을 고친다.
 */
function entry(over: Partial<PhotoEntry>): PhotoEntry {
  return {
    id: "pe",
    kind: "event",
    title: "지어낸 항목",
    date: null,
    items: [],
    needs_review: false,
    past: false,
    ...over,
  };
}

const timed = entry({
  date: "2026-11-23",
  all_day: false,
  starts_at: "2026-11-23T09:30:00+09:00",
  ends_at: "2026-11-23T11:00:00+09:00",
});

const span = entry({
  date: "2026-11-23",
  all_day: true,
  starts_at: "2026-11-23T00:00:00+09:00",
  ends_at: "2026-11-27T23:59:00+09:00",
});

const due = entry({
  kind: "todo",
  date: "2026-11-14",
  all_day: true,
  starts_at: null,
  due_at: "2026-11-14T23:59:00+09:00",
});

describe("entryDateRole", () => {
  it("시작을 읽었으면 종류와 상관없이 시작이다", () => {
    expect(entryDateRole(timed)).toBe("start");
    expect(entryDateRole({ ...due, starts_at: "2026-11-10T00:00:00+09:00" })).toBe("start");
  });

  it("시작이 없는 할 일은 마감이다", () => {
    expect(entryDateRole(due)).toBe("due");
  });

  it("🚨 일정 항목이어도 마감만 읽었으면 마감이다 — '4월 30일까지' 를 시작으로 넣지 않는다", () => {
    expect(entryDateRole({ ...due, kind: "event" })).toBe("due");
  });

  it("일정 항목은 아무것도 못 읽었어도 시작이다 — 서버도 그 날을 시작으로 고친다", () => {
    expect(entryDateRole(entry({ kind: "event" }))).toBe("start");
    expect(entryDateRole(entry({ kind: "todo" }))).toBe("due");
  });
});

describe("entryDateLabel", () => {
  it("일정으로 넣을 수 있는 항목의 마감만 마감이다", () => {
    expect(entryDateLabel(due)).toBe("마감");
    expect(entryDateLabel({ ...due, kind: "event" })).toBe("마감");
    expect(entryDateLabel(timed)).toBe("언제");
  });

  it("🚨 급식과 안내는 판정이 마감이어도 언제다 (#792 리뷰)", () => {
    // 급식은 `date` 만 오고 starts_at · due_at 이 없다.
    const meal = entry({ kind: "meal", date: "2026-11-09" });
    expect(entryDateRole(meal)).toBe("due");
    expect(entryDateLabel(meal)).toBe("언제");
    expect(entryDateLabel(entry({ kind: "info" }))).toBe("언제");
  });
});

describe("droppedEnd", () => {
  it("여러 날 기간이 빠지면 span 이다", () => {
    expect(droppedEnd(span, { startsAt: "2026-11-24T00:00:00+09:00", endsAt: null })).toBe("span");
  });

  it("🚨 같은 날 끝나는 시각이 빠져도 말한다 (#792 리뷰)", () => {
    expect(droppedEnd(timed, { startsAt: "2026-11-23T11:30:00+09:00", endsAt: null })).toBe("time");
  });

  it("끝을 그대로 싣거나, 시작이 비었거나, 하루 종일 하루짜리면 말하지 않는다", () => {
    expect(
      droppedEnd(timed, {
        startsAt: "2026-11-23T09:30:00+09:00",
        endsAt: timed.ends_at ?? null,
      }),
    ).toBeNull();
    expect(droppedEnd(timed, { startsAt: null, endsAt: null })).toBeNull();
    const oneDay = { ...span, ends_at: "2026-11-23T23:59:00+09:00" };
    expect(droppedEnd(oneDay, { startsAt: "2026-11-23T10:00:00+09:00", endsAt: null })).toBeNull();
  });
});

describe("canBecomeEvent", () => {
  it("급식과 안내에는 일정 칸이 없다", () => {
    expect(canBecomeEvent(entry({ kind: "meal" }))).toBe(false);
    expect(canBecomeEvent(entry({ kind: "info" }))).toBe(false);
    expect(canBecomeEvent(entry({ kind: "event" }))).toBe(true);
    expect(canBecomeEvent(due)).toBe(true);
  });
});

describe("readSpan", () => {
  it("시작과 끝이 다른 날이면 기간이다", () => {
    expect(readSpan(span)).toEqual({ from: "2026-11-23", to: "2026-11-27" });
  });

  it("같은 날에 끝나면 기간이 아니다 (종일 하루의 23:59 도)", () => {
    expect(readSpan(timed)).toBeNull();
    expect(readSpan({ ...span, ends_at: "2026-11-23T23:59:00+09:00" })).toBeNull();
  });

  it("🚨 시작일을 고쳤으면 기간이 없다 — 저장되는 것은 그날 하루다", () => {
    expect(readSpan(span, "2026-11-24")).toBeNull();
    expect(readSpan({ ...span, date: "2026-11-24" })).toBeNull();
  });

  it("마감 항목에는 기간이 없다", () => {
    expect(readSpan({ ...due, ends_at: "2026-11-20T23:59:00+09:00" })).toBeNull();
  });
});

describe("readMoment", () => {
  it("시각까지 읽은 시작을 돌려준다", () => {
    expect(readMoment(timed)).toBe(timed.starts_at);
  });

  it("시각까지 읽은 마감을 돌려준다", () => {
    const at = "2026-11-14T18:00:00+09:00";
    expect(readMoment({ ...due, all_day: false, due_at: at })).toBe(at);
  });

  it("하루 종일이거나 날짜를 못 읽었으면 없다", () => {
    expect(readMoment(span)).toBeNull();
    expect(readMoment(due)).toBeNull();
    expect(readMoment({ ...timed, date: null })).toBeNull();
  });

  it("🚨 보호자가 날짜를 고쳤으면 없다 — 서버가 시각을 버린다", () => {
    expect(readMoment({ ...timed, date: "2026-11-24" })).toBeNull();
  });
});

describe("readStartTime", () => {
  it("읽은 시각을 서울 시각으로 채운다", () => {
    expect(readStartTime(timed)).toBe("09:30");
    // 표기가 UTC 여도 서울 시각이다.
    expect(readStartTime({ ...timed, starts_at: "2026-11-23T00:30:00Z" })).toBe("09:30");
  });

  it("🚨 하루 종일이거나 시각을 못 읽었으면 비운다 — 자정으로 채우지 않는다", () => {
    expect(readStartTime(span)).toBe("");
    expect(readStartTime({ ...timed, starts_at: null })).toBe("");
    expect(readStartTime({ ...timed, all_day: undefined })).toBe("");
  });

  it("마감의 시각은 시작이 아니다", () => {
    expect(readStartTime({ ...due, all_day: false, due_at: "2026-11-14T18:00:00+09:00" })).toBe("");
  });
});

describe("carriedEndsAt", () => {
  it("읽은 그대로면 끝을 싣는다", () => {
    expect(
      carriedEndsAt(span, {
        date: "2026-11-23",
        allDay: true,
        startsAt: "2026-11-23T00:00:00+09:00",
      }),
    ).toBe(span.ends_at);
    expect(
      carriedEndsAt(timed, {
        date: "2026-11-23",
        allDay: false,
        startsAt: "2026-11-23T09:30:00+09:00",
      }),
    ).toBe(timed.ends_at);
  });

  it("🚨 시작 날짜를 고치면 싣지 않는다", () => {
    expect(
      carriedEndsAt(span, {
        date: "2026-11-24",
        allDay: true,
        startsAt: "2026-11-24T00:00:00+09:00",
      }),
    ).toBeNull();
  });

  it("🚨 하루 종일을 바꾸면 싣지 않는다", () => {
    expect(
      carriedEndsAt(span, {
        date: "2026-11-23",
        allDay: false,
        startsAt: "2026-11-23T10:00:00+09:00",
      }),
    ).toBeNull();
  });

  it("🚨 고친 시각이 끝보다 늦으면 싣지 않는다 — 서버가 400 으로 막는다", () => {
    expect(
      carriedEndsAt(timed, {
        date: "2026-11-23",
        allDay: false,
        startsAt: "2026-11-23T11:00:00+09:00",
      }),
    ).toBeNull();
  });

  it("시작이 비었거나 마감 항목이면 싣지 않는다", () => {
    expect(carriedEndsAt(span, { date: "2026-11-23", allDay: true, startsAt: null })).toBeNull();
    expect(
      carriedEndsAt(
        { ...due, ends_at: "2026-11-20T23:59:00+09:00" },
        { date: "2026-11-10", allDay: true, startsAt: "2026-11-10T00:00:00+09:00" },
      ),
    ).toBeNull();
  });
});

/* ── 캘린더에 넣기 (#824) ─────────────────────────────────────────────── */

describe("startsChecked", () => {
  it("시작 날짜를 읽은 일정만 처음부터 켠다", () => {
    expect(startsChecked(timed)).toBe(true);
    expect(startsChecked(span)).toBe(true);
    // 날짜만 읽고 시각이 없으면 하루 종일로 넣는다 — 그래도 넣을 수 있어서 켠다.
    expect(startsChecked(entry({ date: "2026-11-23" }))).toBe(true);
  });

  it("🚨 할 일은 켜지 않는다 — 보호자가 고른다", () => {
    expect(startsChecked(due)).toBe(false);
    expect(startsChecked({ ...due, kind: "event" })).toBe(false); // 마감만 읽은 일정도 그 날은 시작이 아니다
  });

  it("🚨 확인이 필요한 일정은 켜지 않는다 — 저장이 그 항목을 받지 않는다", () => {
    expect(startsChecked({ ...timed, needs_review: true })).toBe(false);
  });

  it("🚨 지난 일정은 켜지 않는다 — 지났는지는 서버가 정한다 (#828 리뷰)", () => {
    expect(startsChecked({ ...timed, past: true })).toBe(false);
    expect(startsChecked({ ...span, past: true })).toBe(false);
  });

  it("날짜가 없거나, 시각을 읽어야 하는데 없으면 켜지 않는다", () => {
    expect(startsChecked(entry({ date: null }))).toBe(false);
    expect(startsChecked(entry({ date: "2026-11-23", all_day: false }))).toBe(false);
  });
});

describe("offBecausePast", () => {
  it("지난 일정이라는 것 하나 때문에 꺼 둔 줄만이다", () => {
    expect(offBecausePast({ ...timed, past: true })).toBe(true);
    expect(offBecausePast(timed)).toBe(false);
  });

  it("🚨 지난 할 일은 아니다 — 할 일은 지나지 않았어도 켜지 않는다", () => {
    expect(offBecausePast({ ...due, past: true })).toBe(false);
  });

  it("다른 까닭으로도 꺼질 줄은 아니다", () => {
    expect(offBecausePast({ ...timed, past: true, needs_review: true })).toBe(false);
  });
});

describe("planLocks", () => {
  it("읽은 값 그대로 넣을 수 있으면 비어 있다", () => {
    expect(planLocks(timed, initialPlan(timed))).toEqual([]);
  });

  it("🚨 할 일은 시작하는 날을 고르기 전에는 잠긴다 — 마감일로 넣지 않는다", () => {
    expect(planLocks(due, initialPlan(due))).toEqual([
      "고치기에서 시작하는 날을 고르면 넣을 수 있어요.",
    ]);
    expect(planLocks(due, { ...initialPlan(due), startDate: "2026-11-10" })).toEqual([]);
  });

  it("확인이 필요한 항목은 그 하나만 말한다", () => {
    expect(planLocks({ ...timed, needs_review: true }, initialPlan(timed))).toEqual([
      "고치기에서 확인하면 넣을 수 있어요.",
    ]);
  });

  it("막는 것이 여럿이면 전부 말한다", () => {
    const blank = entry({ date: null });
    expect(planLocks(blank, { ...initialPlan(blank), allDay: false, time: "" })).toEqual([
      "고치기에서 날짜를 고르면 넣을 수 있어요.",
      "고치기에서 시작 시간을 고르면 넣을 수 있어요.",
    ]);
  });

  it("🚨 지난 알림은 조용히 빼지 않고 막는다", () => {
    const plan = { ...initialPlan(timed), reminder: "1h" as const };
    const after = Date.parse("2026-11-23T09:00:00+09:00");
    expect(planLocks(timed, plan, after)).toEqual([
      "고른 알림 시각이 이미 지났어요. 고치기에서 알림을 다시 골라 주세요.",
    ]);
  });
});

describe("eventBody", () => {
  it("읽은 시각과 끝을 그대로 싣고, 알림은 없음이다", () => {
    expect(eventBody({ ...timed, items: ["물병"] }, initialPlan(timed))).toEqual({
      event: {
        title: "지어낸 항목",
        starts_at: "2026-11-23T09:30:00+09:00",
        ends_at: "2026-11-23T11:00:00+09:00",
        all_day: false,
        event_type: "episodic",
        category: "institution",
      },
      items: [{ item_id: null, item_name: "물병" }],
      reminder: null,
    });
  });

  it("🚨 할 일은 고른 시작하는 날로 넣는다 — 마감일이 아니다", () => {
    const body = eventBody(due, { ...initialPlan(due), startDate: "2026-11-10" });
    expect(body?.event).toMatchObject({
      starts_at: "2026-11-10T00:00:00+09:00",
      ends_at: null,
      all_day: true,
    });
  });

  it("넣을 수 없으면 null 이다", () => {
    expect(eventBody(due, initialPlan(due))).toBeNull();
  });
});

describe("eventWhen", () => {
  it("여러 날이면 기간, 같은 날 끝나는 시각이 있으면 그것까지 쓴다", () => {
    expect(eventWhen(span.starts_at!, span.ends_at!, true)).toMatch(/23일.*부터 .*27일.*까지/);
    expect(eventWhen(timed.starts_at!, timed.ends_at!, false)).toMatch(/9:30부터 \S+ 11:00까지$/);
  });

  it("🚨 하루 종일 하루짜리의 끝(그날 23:59)은 말하지 않는다", () => {
    expect(eventWhen("2026-11-23T00:00:00+09:00", "2026-11-23T23:59:00+09:00", true)).toMatch(
      /하루 종일$/,
    );
  });
});

/**
 * 저장되는 기록의 값. 🚨 보호자가 시트에서 고친 시작이 캘린더에만 들어가고 기록에는 사진에서 읽은
 * 시각이 남던 것을 막는다 — 목록의 날짜 줄과 저장 요청이 이 값을 쓴다. 서버 `apply_edits` 가 받는 값이다.
 */
describe("recordedEntry", () => {
  it("🚨 시작 시간을 고치면 기록의 시작도 고친 시각이다", () => {
    const record = recordedEntry(timed, { ...initialPlan(timed), time: "20:00" });

    expect(record.starts_at).toBe("2026-11-23T20:00:00+09:00");
    expect(record.all_day).toBe(false);
    // 읽은 끝(11:00)은 고친 시작보다 앞이라 싣지 않는다 — 캘린더에 넣는 값과 같다.
    expect(record.ends_at).toBeNull();
    // 목록의 날짜 줄이 이 값으로 고친 시각을 말한다.
    expect(readMoment(record)).toBe("2026-11-23T20:00:00+09:00");
  });

  it("고치지 않았으면 읽은 값과 같은 순간이고 읽은 끝도 그대로다", () => {
    const record = recordedEntry(timed, initialPlan(timed));

    expect(Date.parse(record.starts_at ?? "")).toBe(Date.parse(timed.starts_at ?? ""));
    expect(record.ends_at).toBe(timed.ends_at);
    expect(record.all_day).toBe(false);
  });

  it("여러 날을 읽은 하루 종일 일정은 고치지 않으면 기간이 그대로다", () => {
    const record = recordedEntry(span, initialPlan(span));

    expect(readSpan(record)).toEqual({ from: "2026-11-23", to: "2026-11-27" });
    expect(record.all_day).toBe(true);
  });

  it("시각을 읽은 일정을 하루 종일로 바꾸면 기록도 하루 종일이다", () => {
    const record = recordedEntry(timed, { ...initialPlan(timed), allDay: true });

    expect(record.all_day).toBe(true);
    expect(record.starts_at).toBe("2026-11-23T00:00:00+09:00");
    expect(readMoment(record)).toBeNull();
  });

  it("날짜와 시각을 함께 고치면 고친 날의 그 시각이다", () => {
    const moved = { ...timed, date: "2026-11-25" };
    const record = recordedEntry(moved, { ...initialPlan(timed), time: "20:00" });

    expect(record.starts_at).toBe("2026-11-25T20:00:00+09:00");
    expect(record.ends_at).toBeNull();
  });

  it("🚨 마감 항목은 건드리지 않는다 — 시작하는 날과 시작 시간은 캘린더에 넣을 때만 쓴다", () => {
    const plan = { ...initialPlan(due), startDate: "2026-11-10", allDay: false, time: "09:00" };

    expect(recordedEntry(due, plan)).toBe(due);
  });

  it("급식과 안내는 건드리지 않는다", () => {
    const meal = entry({ kind: "meal", date: "2026-11-23", all_day: true });
    const info = entry({ kind: "info", date: "2026-11-23", all_day: true });

    expect(recordedEntry(meal, initialPlan(meal))).toBe(meal);
    expect(recordedEntry(info, initialPlan(info))).toBe(info);
  });

  it("하루 종일을 껐는데 시각을 아직 안 골랐으면 읽은 값 그대로다", () => {
    const allDay = entry({
      date: "2026-11-23",
      all_day: true,
      starts_at: "2026-11-23T00:00:00+09:00",
    });

    expect(recordedEntry(allDay, { ...initialPlan(allDay), allDay: false, time: "" })).toBe(allDay);
  });
});
