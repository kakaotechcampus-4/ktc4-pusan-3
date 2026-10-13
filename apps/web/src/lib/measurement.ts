/**
 * 키·몸무게 입력값 읽기. 11-1 측정 기록 시트와 02 온보딩이 **같은 규칙**을 쓴다.
 *
 * 빈 칸은 `null`, 숫자가 아니면 `"invalid"`, 서버가 받는 범위 밖이면 `"out_of_range"`.
 *
 * 🚨 **잘못 적은 값을 조용히 `0` 이나 `NaN` 으로 넘기지 않는다** — 아이 몸무게가 0kg 으로
 *    쌓인다. 그 기록은 11-1 그래프에 그대로 그려지고, 보호자가 넣은 숫자를 그대로 그리는
 *    화면이라 아무도 그것을 고쳐 주지 않는다.
 * 🚨 **`null`(안 잼)과 `0`(0으로 쟀다)은 다르다.** 한쪽만 재고 오는 날이 있다.
 */
export type MeasurementRead = number | null | "invalid" | "out_of_range";

/**
 * 서버(`MEASUREMENT_MAX` · `apps/api/app/api/v1/schemas/children.py`)와 같은 상한.
 * 🚨 **평가가 아니라 오타 막기다** — 아이 키로 300 을 넘는 값은 단위나 자릿수를 잘못 친 것이다.
 */
export const MEASUREMENT_MAX = 300;

/**
 * 서버는 소수 한 자리로 반올림해 저장하고, 반올림해서 0 이 되는 값(0.04)은 잰 것이 아니라 400 이다.
 * 반올림해서 0.1 이 되는 가장 작은 값이 0.05 다.
 */
const MEASUREMENT_MIN = 0.05;

/**
 * 🚨 **범위를 화면이 먼저 막는 이유** — 서버 400 은 화면에서 "다시 시도" 로만 보이는데,
 *    같은 값을 다시 보내면 계속 400 이다. 어느 칸이 왜 안 되는지를 그 칸에 붙인다.
 */
export function parseMeasurement(value: string): MeasurementRead {
  const trimmed = value.trim();
  if (!trimmed) return null;
  const parsed = Number(trimmed);
  if (!Number.isFinite(parsed) || parsed <= 0) return "invalid";
  if (!isMeasurementInRange(parsed)) return "out_of_range";
  return parsed;
}

/** 서버가 받는 범위인가. 목도 이것으로 서버의 400 을 흉내 낸다. */
export function isMeasurementInRange(value: number): boolean {
  return value >= MEASUREMENT_MIN && value <= MEASUREMENT_MAX;
}

/** 읽은 결과를 칸에 붙일 문구로. 숫자나 빈 칸이면 `undefined`. */
export function measurementError(read: MeasurementRead): string | undefined {
  if (read === "invalid") return "숫자로 적어주세요";
  if (read === "out_of_range") return `0.1부터 ${MEASUREMENT_MAX}까지 적을 수 있어요`;
  return undefined;
}
