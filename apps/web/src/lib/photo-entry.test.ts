import { describe, expect, it } from "vitest";

import type { PhotoEntry } from "@/lib/api/types";
import {
  canBecomeEvent,
  carriedEndsAt,
  entryDateRole,
  readMoment,
  readSpan,
  readStartTime,
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

  it("🚨 고친 시각이 끝보다 늦으면 싣지 않는다 — 서버가 422 로 막는다", () => {
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
