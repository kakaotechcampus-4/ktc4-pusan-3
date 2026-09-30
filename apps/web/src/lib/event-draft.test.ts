import { describe, expect, it } from "vitest";

import type { EventDraftFields } from "@/lib/api/types";
import { moveStart, seoulDate, seoulTime, withSeoulDateTime } from "@/lib/event-draft";

/**
 * 🚨 **여기서 거는 것은 "모르는 값을 채우지 않는다" 하나다** (CLAUDE.md §2 · #151).
 *
 * 화면 테스트가 아니라 조립 함수의 단위 테스트인 이유 — 자정으로 접히는 버그는 칸이 아니라
 * **조립에서** 났다. 날짜만 고른 상태를 `starts_at` 하나로 들면 담을 자리가 없어서 자정이 된다.
 */
describe("일시 조립", () => {
  const event: EventDraftFields = {
    title: "물놀이",
    starts_at: "2026-10-04T15:00:00+09:00",
    ends_at: "2026-10-04T17:00:00+09:00",
    all_day: false,
    event_type: "episodic",
    category: "activity",
  };

  it("🚨 시각을 안 골랐으면 자정이 아니라 null 이다", () => {
    // 이 한 줄이 이 파일의 이유다 — 예전에는 여기서 자정이 나와 승인 게이트를 지났다.
    expect(withSeoulDateTime("2026-10-04", "", false)).toBeNull();
  });

  it("날짜를 안 골랐으면 null 이다 — 시각만으로는 아무것도 안 된다", () => {
    expect(withSeoulDateTime("", "18:30", false)).toBeNull();
  });

  it("🚨 하루 종일만 자정을 쓴다 — 시각을 안 묻는 것이지 자정을 고른 것이 아니다", () => {
    // 시각이 비어 있어도 나가고, `all_day: true` 가 그 사실을 함께 나른다.
    expect(withSeoulDateTime("2026-10-04", "", true)).toBe("2026-10-04T00:00:00+09:00");
  });

  it("둘 다 고르면 서울 기준으로 조립한다 — 기기 시간대를 타지 않는다", () => {
    expect(withSeoulDateTime("2026-10-04", "18:30", false)).toBe("2026-10-04T18:30:00+09:00");
  });

  it("읽어온 값은 서울 기준으로 되읽는다", () => {
    // UTC 로 온 같은 순간도 서울에서 본 날짜·시각이어야 한다.
    expect(seoulDate("2026-10-04T09:30:00Z")).toBe("2026-10-04");
    expect(seoulTime("2026-10-04T09:30:00Z")).toBe("18:30");
  });

  it("값이 없으면 빈 문자열이다 — 오늘·자정으로 채우지 않는다", () => {
    expect(seoulDate(null)).toBe("");
    expect(seoulTime(null)).toBe("");
  });

  it("🚨 시작을 옮기면 끝이 걸린 시간을 유지한 채 따라온다", () => {
    const moved = moveStart(event, "2026-10-05T10:00:00+09:00");

    expect(moved.starts_at).toBe("2026-10-05T10:00:00+09:00");
    // 두 시간짜리 일정은 옮겨도 두 시간이다 (끝이 시작보다 앞서지 않는다).
    expect(Date.parse(moved.ends_at!) - Date.parse(moved.starts_at!)).toBe(2 * 60 * 60 * 1000);
  });

  it("🚨 시작이 사라지면 끝도 사라진다 — 끝만 남은 일정은 말이 안 된다", () => {
    expect(moveStart(event, null)).toEqual({ starts_at: null, ends_at: null });
  });

  it("끝이 없던 일정에 끝을 만들지 않는다", () => {
    const moved = moveStart({ ...event, ends_at: null }, "2026-10-05T10:00:00+09:00");

    expect(moved.starts_at).toBe("2026-10-05T10:00:00+09:00");
    expect(moved.ends_at).toBeUndefined();
  });
});
