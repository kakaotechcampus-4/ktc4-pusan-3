/**
 * 키·몸무게 입력값 읽기. 11-1 측정 기록 시트와 02 온보딩이 **같은 규칙**을 쓴다.
 *
 * 빈 칸은 `null`, 숫자가 아니면 `"invalid"`, 범위 밖이면 `"out_of_range"`,
 * 소수 둘째 자리부터 적었으면 `"too_precise"`.
 *
 * 🚨 **잘못 적은 값을 조용히 `0` 이나 `NaN` 으로 넘기지 않는다** — 아이 몸무게가 0kg 으로
 *    쌓인다. 그 기록은 11-1 그래프에 그대로 그려지고, 보호자가 넣은 숫자를 그대로 그리는
 *    화면이라 아무도 그것을 고쳐 주지 않는다.
 * 🚨 **`null`(안 잼)과 `0`(0으로 쟀다)은 다르다.** 한쪽만 재고 오는 날이 있다.
 */
export type MeasurementRead = number | null | "invalid" | "too_precise" | "out_of_range";

export type MeasurementKind = "height" | "weight";

/**
 * 칸마다 받는 범위 (#627). 서버 `HeightCm` · `WeightKg` 와 같은 값이다
 * (`apps/api/app/api/v1/schemas/children.py` · #628). 바꾸면 양쪽을 같이 바꾼다.
 *
 * 🚨 **평가가 아니라 오타 막기다** (최상위 §2). 실제 아이가 있을 수 있는 값은 넉넉히 감싸고
 *    (이른둥이부터 서비스 상한 나이인 20세까지), **단위 착각만** 잡는다 — 키를 m 나 mm 로,
 *    몸무게를 g 으로 적은 값이 여기서 걸린다. 한 범위를 같이 쓰던 때는 키 1cm 와 몸무게
 *    300kg 이 그대로 저장돼 그래프에 그려졌다.
 */
export const MEASUREMENT_RANGE = {
  height: { min: 20, max: 220, unit: "cm" },
  weight: { min: 0.3, max: 150, unit: "kg" },
} as const satisfies Record<MeasurementKind, { min: number; max: number; unit: string }>;

/**
 * 숫자와 소수점만. 🚨 `Number()` 에 바로 넘기지 않는다 — `1e2` · `0x64` 를 100 으로 읽는다.
 */
const NUMBER_PATTERN = /^(?:\d+\.?\d*|\.\d+)$/;

/**
 * 🚨 **범위를 화면이 먼저 막는 이유** — 서버 400 은 화면에서 "다시 시도" 로만 보이는데,
 *    같은 값을 다시 보내면 계속 400 이다. 어느 칸이 왜 안 되는지를 그 칸에 붙인다.
 */
export function parseMeasurement(kind: MeasurementKind, value: string): MeasurementRead {
  // 전각 숫자(`１０４．２`)도 보호자에게는 숫자다. NFKC 가 반각으로 접는다.
  const trimmed = value.normalize("NFKC").trim();
  if (!trimmed) return null;
  if (!NUMBER_PATTERN.test(trimmed)) return "invalid";

  const parsed = Number(trimmed);
  if (!isMeasurementInRange(kind, parsed)) return "out_of_range";

  /**
   * 🚨 **소수 둘째 자리부터는 받지 않는다.** 서버도 반올림하지 않고 400 으로 받는다 (#628) —
   *    반올림해 저장하면 적은 값과 보이는 값이 말없이 달라진다. 범위와 같은 이유로 화면이 먼저
   *    막아 그 칸에 이유를 붙인다. 끝의 0(`104.20`)은 같은 값이라 센다 (서버도 받는다).
   */
  const decimals = (trimmed.split(".")[1] ?? "").replace(/0+$/, "");
  if (decimals.length > 1) return "too_precise";

  return parsed;
}

/** 그 칸이 받는 범위인가. 목도 이것으로 서버의 400 을 흉내 낸다. */
export function isMeasurementInRange(kind: MeasurementKind, value: number): boolean {
  const { min, max } = MEASUREMENT_RANGE[kind];
  return value >= min && value <= max;
}

/**
 * 읽은 결과를 칸에 붙일 문구로. 숫자나 빈 칸이면 `undefined`.
 * 🚨 범위 밖은 **단위를 같이 말한다** — 가장 흔한 원인이 단위 착각이다. "정상" · "또래" 처럼
 *    평가로 읽히는 말은 쓰지 않는다 (최상위 §2).
 */
export function measurementError(kind: MeasurementKind, read: MeasurementRead): string | undefined {
  if (read === "invalid") return "숫자만 적어주세요";
  if (read === "too_precise") return "소수점 아래 한 자리까지 적어주세요";
  if (read === "out_of_range") {
    const { min, max, unit } = MEASUREMENT_RANGE[kind];
    return `${unit} 단위로 ${min}부터 ${max}까지 적을 수 있어요`;
  }
  return undefined;
}
