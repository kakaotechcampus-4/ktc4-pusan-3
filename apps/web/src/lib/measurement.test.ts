import { describe, expect, it } from "vitest";

import {
  MEASUREMENT_RANGE,
  isMeasurementInRange,
  measurementError,
  parseMeasurement,
} from "./measurement";

/**
 * 키·몸무게 읽기 — 02 온보딩과 11-1 시트가 같이 쓴다. 범위는 칸마다 다르다 (#627).
 * 🚨 오타 막기다. 단위를 잘못 고른 값만 걸리고, 실제 아이 값은 다 통과해야 한다.
 */
describe("parseMeasurement", () => {
  it("빈 칸은 안 잰 것이다 — 0 이 아니다", () => {
    expect(parseMeasurement("height", "")).toBeNull();
    expect(parseMeasurement("weight", "   ")).toBeNull();
  });

  // 🚨 `Number()` 는 `1e2` · `0x64` 를 100 으로 읽는다. 숫자와 소수점만 받는다.
  it.each(["abc", "-3", "Infinity", "1e2", "0x64", "104cm", "104,2", "1 04"])(
    "%s 는 숫자로 읽지 않는다",
    (value) => {
      expect(parseMeasurement("height", value)).toBe("invalid");
    },
  );

  it.each([
    ["height", "104.2", 104.2],
    ["height", " 104 ", 104],
    ["height", "104.", 104],
    ["height", "104.20", 104.2],
    ["height", "１０４．２", 104.2],
    ["weight", "17.1", 17.1],
    ["weight", ".5", 0.5],
  ] as const)("%s 칸의 %s 는 %s 로 읽는다", (kind, value, expected) => {
    expect(parseMeasurement(kind, value)).toBe(expected);
  });

  it.each(["height", "weight"] as const)("%s 칸의 양 끝 값은 받는다", (kind) => {
    const { min, max } = MEASUREMENT_RANGE[kind];
    expect(parseMeasurement(kind, String(min))).toBe(min);
    expect(parseMeasurement(kind, String(max))).toBe(max);
  });

  // 🚨 단위 착각: 키를 m(1.04) · mm(1042) 로, 몸무게를 g(17100) 으로 적은 값.
  //    한 범위(0.05 ~ 300)를 같이 쓰던 때는 키 1cm 와 몸무게 300kg 이 저장됐다.
  it.each([
    ["height", "0"],
    ["height", "1"],
    ["height", "1.04"],
    ["height", "1042"],
    ["height", "220.1"],
    ["weight", "0"],
    ["weight", "0.2"],
    ["weight", "150.1"],
    ["weight", "300"],
    ["weight", "17100"],
  ] as const)("%s 칸의 %s 는 범위 밖이다", (kind, value) => {
    expect(parseMeasurement(kind, value)).toBe("out_of_range");
  });

  // 🚨 서버가 소수 한 자리로 반올림해 저장한다 — 104.25 를 보내면 104.3 이 남는다.
  it.each([
    ["height", "104.25"],
    ["weight", "17.15"],
  ] as const)("%s 칸의 %s 는 소수 둘째 자리라 받지 않는다", (kind, value) => {
    expect(parseMeasurement(kind, value)).toBe("too_precise");
  });
});

describe("isMeasurementInRange", () => {
  it("같은 값도 칸마다 다르게 본다", () => {
    expect(isMeasurementInRange("height", 160)).toBe(true);
    expect(isMeasurementInRange("weight", 160)).toBe(false);
    expect(isMeasurementInRange("height", 3)).toBe(false);
    expect(isMeasurementInRange("weight", 3)).toBe(true);
  });
});

describe("measurementError", () => {
  it("숫자와 빈 칸에는 문구가 없다", () => {
    expect(measurementError("height", 104.2)).toBeUndefined();
    expect(measurementError("height", null)).toBeUndefined();
  });

  it("못 읽은 값, 너무 잘게 적은 값, 범위 밖 값은 다른 문구다", () => {
    expect(measurementError("height", "invalid")).toBe("숫자만 적어주세요");
    expect(measurementError("height", "too_precise")).toBe("소수점 아래 한 자리까지 적어주세요");
  });

  it("범위 밖은 그 칸의 단위와 범위를 말한다", () => {
    expect(measurementError("height", "out_of_range")).toBe(
      "cm 단위로 20부터 220까지 적을 수 있어요",
    );
    expect(measurementError("weight", "out_of_range")).toBe(
      "kg 단위로 0.3부터 150까지 적을 수 있어요",
    );
  });
});
