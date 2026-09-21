/**
 * 날짜 **입력 제약**. 🚨 날짜·나이 계산이 아니다 — 계산은 서버가 한다
 * (apps/web/CLAUDE.md §4). 고를 수 있는 범위를 정하는 것과 입력 기본값만 여기 둔다.
 *
 * 아이 프로필(11)과 그 아래 "키 · 몸무게" 상세가 같은 값을 쓴다. 한 곳에 두지 않으면
 * 한쪽 달력만 20년, 다른 쪽만 10년이 되는 식으로 조용히 어긋난다.
 */

/** 달력의 하한. 아이 서비스라 20년 전이면 충분하다 (01 화면과 같은 값). */
export const EARLIEST_BIRTH_DATE = new Date(new Date().getFullYear() - 20, 0, 1);

/**
 * 오늘을 `YYYY-MM-DD` 로. 🚨 `toISOString()` 으로 만들지 않는다 — UTC 로 접혀서 한국 시간
 * 자정~오전 9시 사이에는 **하루가 밀린다** (`lib/format.ts` 의 같은 주의).
 */
export function toToday(): string {
  const now = new Date();
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
}
