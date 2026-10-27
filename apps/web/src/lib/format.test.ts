import { describe, expect, it } from "vitest";

import { formatDateRange, formatTimeOfDay } from "./format";

/**
 * 건강 기록의 시각(`observed_time`)은 서버가 ISO 로 보낸다 (#485). 화면은 시각만 그리고,
 * 해석할 수 없는 값이면 원문을 찍지 않고 그 칸을 비운다.
 *
 * ⚠️ "오후" 와 "PM" 은 실행 환경의 ICU 데이터마다 다르게 나와서 글자를 통째로 단언하지 않는다.
 *    한국어 표기 자체는 e2e(`chat.spec.ts`, 브라우저)가 본다.
 */
describe("formatTimeOfDay", () => {
  it("ISO 시각에서 날짜를 빼고 시각만 낸다", () => {
    const text = formatTimeOfDay("2026-10-15T21:00:00+09:00");
    expect(text).toContain("9:00");
    expect(text).not.toContain("2026");
    expect(text).not.toContain("T");
  });

  it("서버가 UTC 로 보내도 한국 시간으로 그린다", () => {
    expect(formatTimeOfDay("2026-10-15T12:00:00Z")).toBe(
      formatTimeOfDay("2026-10-15T21:00:00+09:00"),
    );
  });

  it.each(["밤", ""])("%j 처럼 시각이 아닌 값은 원문 대신 빈 문자열이다", (value) => {
    expect(formatTimeOfDay(value)).toBe("");
  });
});

describe("formatDateRange (#584)", () => {
  it("같은 날이면 하루만 적는다", () => {
    expect(formatDateRange("2026-10-01", "2026-10-01")).toBe("2026년 10월 1일");
  });

  it("같은 해면 끝일의 연도를 뺀다", () => {
    expect(formatDateRange("2026-10-01", "2026-10-31")).toBe("2026년 10월 1일부터 10월 31일까지");
  });

  it("해가 다르면 두 끝 모두 연도를 적는다", () => {
    expect(formatDateRange("2025-12-28", "2026-01-03")).toBe(
      "2025년 12월 28일부터 2026년 1월 3일까지",
    );
  });
});
