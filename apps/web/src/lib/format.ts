/**
 * 서버가 준 **절대 시각을 그대로 표시**하기 위한 변환.
 *
 * 🚨 날짜·나이 **계산**은 프론트가 하지 않는다 (CLAUDE.md §3 — 100% 맞아야 하는 것은 코드가,
 *    그것도 서버가 한다). `age_display` · `observed_label` · `state_reason` 처럼 **판단이 섞인
 *    문구**는 전부 서버가 만들어 내려주고, 프론트는 그걸 그대로 그린다.
 *
 *    여기 있는 것은 그 규칙의 예외가 아니라 **다른 일**이다 — 상대 시간("3일 뒤")도, 기간 차이도,
 *    나이도 만들지 않는다. ISO 절대 시각 하나를 사람이 읽는 표기로 바꾸기만 한다.
 *
 * ⚠️ 시간대를 기기 설정이 아니라 `Asia/Seoul` 로 고정한다. 승인 시트는 **부모가 캘린더에
 *    무엇을 넣는지 확인하는 자리**라, 기기 시간대가 다르면(해외 로밍·잘못된 설정) 서버가 만든
 *    일정과 다른 시각이 보인다. 확인하고 넣은 시각과 실제로 들어간 시각이 다르면 승인 게이트가
 *    무의미해진다.
 *
 * ⚠️ 서버 렌더에서 부르지 않는다. 승인 시트는 `AuthGate` 아래라 클라이언트에서만 그려진다.
 */
const TIME_ZONE = "Asia/Seoul";

const DATE_TIME = new Intl.DateTimeFormat("ko-KR", {
  timeZone: TIME_ZONE,
  month: "long",
  day: "numeric",
  weekday: "short",
  hour: "numeric",
  minute: "2-digit",
});

const DATE_ONLY = new Intl.DateTimeFormat("ko-KR", {
  timeZone: TIME_ZONE,
  month: "long",
  day: "numeric",
  weekday: "short",
});

/** 일정 시각 한 줄. `all_day` 면 시각을 빼고 날짜만 낸다. */
export function formatEventTime(iso: string, allDay = false): string {
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return "";
  return allDay ? `${DATE_ONLY.format(at)} 하루 종일` : DATE_TIME.format(at);
}
