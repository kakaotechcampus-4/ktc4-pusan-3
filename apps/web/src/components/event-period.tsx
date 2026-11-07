import { formatDateRange, parseISODate } from "@/lib/format";

/**
 * 행사가 붙은 놀이 추천의 기간 한 줄 (#681). 05 개인화 줄과 일반 카드가 같이 쓴다.
 * 확인 문구(`place_notice`) 바로 위에 같은 글씨로 선다 — 둘 다 제안 문장에 딸린 말이다.
 *
 * 날짜 없이 행사 이름만 나가면 지난 행사인지, 언제까지 열리는지 고르기 전에 알 수 없다 (#516).
 *
 * 🚨 **`~` 로 잇지 않는다** — 화면 글자에 기호를 쓰지 않는 규칙(디자인 시스템 §4)이고 스크린리더가 "물결" 로
 *    읽는다. 07 필터와 같은 `formatDateRange` 표기다 ("2026년 9월 29일부터 10월 1일까지").
 * 🚨 **날짜를 더하고 빼지 않는다.** "3일 남았어요" 나 "진행 중" 을 만들지 않는다 — 오늘과 비교하는 순간
 *    화면이 날짜를 계산한다 (`docs/web/screen-rules-v1.md` §2). 서버가 준 두 날짜를 그대로 적는다.
 * 🚨 **근거가 아니다.** "사용한 기록" 목록에 넣지 않는다.
 *
 * 둘 중 하나라도 비었거나 `YYYY-MM-DD` 가 아니면 줄을 세우지 않는다 — 반쪽 기간("부터 까지")을 그리지 않는다.
 */
export function EventPeriod({
  startsOn,
  endsOn,
}: {
  startsOn: string | null;
  endsOn: string | null;
}) {
  const text = eventPeriodText(startsOn, endsOn);
  if (!text) return null;
  return <p className="text-caption text-ink-muted mt-1">{text}</p>;
}

/** 하루짜리면 "2026년 9월 29일에 열려요", 여러 날이면 "2026년 9월 29일부터 10월 1일까지 열려요". */
export function eventPeriodText(startsOn: string | null, endsOn: string | null): string | null {
  if (!startsOn || !endsOn) return null;
  if (!parseISODate(startsOn) || !parseISODate(endsOn)) return null;
  const range = formatDateRange(startsOn, endsOn);
  return startsOn === endsOn ? `${range}에 열려요` : `${range} 열려요`;
}
