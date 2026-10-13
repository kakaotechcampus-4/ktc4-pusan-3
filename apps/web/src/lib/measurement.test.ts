import { describe, expect, it } from "vitest";

import { MEASUREMENT_MAX, measurementError, parseMeasurement } from "./measurement";

/**
 * 키·몸무게 읽기 — 02 온보딩과 11-1 시트가 같이 쓴다. 범위는 서버 `Measurement`
 * (0 보다 크고 300 이하, 소수 한 자리로 반올림해 0.1 이상 · PR #353)와 같아야 한다.
 */
describe("parseMeasurement", () => {
  it("빈 칸은 안 잰 것이다 — 0 이 아니다", () => {
    expect(parseMeasurement("")).toBeNull();
    expect(parseMeasurement("   ")).toBeNull();
  });

  it.each(["abc", "0", "-3", "Infinity"])("%s 는 숫자로 읽지 않는다", (value) => {
    expect(parseMeasurement(value)).toBe("invalid");
  });

  it.each([
    ["0.05", 0.05],
    ["104.2", 104.2],
    [" 17.1 ", 17.1],
    [String(MEASUREMENT_MAX), MEASUREMENT_MAX],
  ])("%s 는 서버가 받는 값이다", (value, expected) => {
    expect(parseMeasurement(value)).toBe(expected);
  });

  // 🚨 서버는 반올림해 0 이 되는 값(0.04)과 300 을 넘는 값을 400 으로 받는다.
  //    화면이 그대로 보내면 "다시 시도" 가 같은 400 을 되풀이한다.
  it.each(["0.04", "300.1", "1042"])("%s 는 범위 밖이다", (value) => {
    expect(parseMeasurement(value)).toBe("out_of_range");
  });
});

describe("measurementError", () => {
  it("숫자와 빈 칸에는 문구가 없다", () => {
    expect(measurementError(104.2)).toBeUndefined();
    expect(measurementError(null)).toBeUndefined();
  });

  it("못 읽은 값과 범위 밖 값은 다른 문구다", () => {
    expect(measurementError("invalid")).toBe("숫자로 적어주세요");
    expect(measurementError("out_of_range")).toBe("0.1부터 300까지 적을 수 있어요");
  });
});
