/**
 * 키·몸무게 입력값 읽기. 11-1 측정 기록 시트와 02 온보딩이 **같은 규칙**을 쓴다.
 *
 * 빈 칸은 `null`, 숫자가 아니면 `"invalid"`.
 *
 * 🚨 **잘못 적은 값을 조용히 `0` 이나 `NaN` 으로 넘기지 않는다** — 아이 몸무게가 0kg 으로
 *    쌓인다. 그 기록은 11-1 그래프에 그대로 그려지고, 보호자가 넣은 숫자를 그대로 그리는
 *    화면이라 아무도 그것을 고쳐 주지 않는다.
 * 🚨 **`null`(안 잼)과 `0`(0으로 쟀다)은 다르다.** 한쪽만 재고 오는 날이 있다.
 */
export function parseMeasurement(value: string): number | null | "invalid" {
  const trimmed = value.trim();
  if (!trimmed) return null;
  const parsed = Number(trimmed);
  if (!Number.isFinite(parsed) || parsed <= 0) return "invalid";
  return parsed;
}
