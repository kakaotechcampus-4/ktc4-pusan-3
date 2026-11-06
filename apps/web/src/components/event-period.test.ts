import { describe, expect, it } from "vitest";

import { eventPeriodText } from "./event-period";

/**
 * 행사 기간 한 줄 (#681). 표기 자체(연도를 언제 빼는지)는 `formatDateRange` 테스트가 본다.
 * 여기서는 행사 줄이 그 위에 더하는 것 — 하루와 여러 날의 끝말, 반쪽 기간을 그리지 않는 것 — 만 본다.
 */
describe("eventPeriodText", () => {
  it("하루짜리 행사는 날짜 하나로 적는다", () => {
    expect(eventPeriodText("2026-11-07", "2026-11-07")).toBe("2026년 11월 7일에 열려요");
  });

  it("여러 날 행사는 부터 까지로 잇고 기호를 쓰지 않는다", () => {
    const text = eventPeriodText("2026-10-30", "2026-11-01");
    expect(text).toBe("2026년 10월 30일부터 11월 1일까지 열려요");
    expect(text).not.toContain("~");
  });

  it("해를 넘기는 행사는 끝날에도 연도를 적는다", () => {
    expect(eventPeriodText("2026-12-31", "2027-01-02")).toBe(
      "2026년 12월 31일부터 2027년 1월 2일까지 열려요",
    );
  });

  it("🚨 한쪽이라도 비었거나 날짜가 아니면 줄을 세우지 않는다", () => {
    expect(eventPeriodText(null, null)).toBeNull();
    expect(eventPeriodText("2026-10-30", null)).toBeNull();
    expect(eventPeriodText(null, "2026-11-01")).toBeNull();
    expect(eventPeriodText("2026-10-30", "")).toBeNull();
    expect(eventPeriodText("10월 30일", "2026-11-01")).toBeNull();
    expect(eventPeriodText("2026-10-30T00:00:00Z", "2026-11-01")).toBeNull();
  });
});
