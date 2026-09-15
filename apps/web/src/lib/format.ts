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

const MONTH_ONLY = new Intl.DateTimeFormat("ko-KR", {
  timeZone: TIME_ZONE,
  year: "numeric",
  month: "long",
});

/** 일정 시각 한 줄. `all_day` 면 시각을 빼고 날짜만 낸다. */
export function formatEventTime(iso: string, allDay = false): string {
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return "";
  return allDay ? `${DATE_ONLY.format(at)} 하루 종일` : DATE_TIME.format(at);
}

/**
 * 날짜 하나를 한국어 표기로 ("9월 12일 (토)"). `YYYY-MM-DD` 와 ISO 시각을 모두 받는다.
 * 🚨 상대 표현("3일 전")을 만들지 않는다 — 그건 `observed_label` 이고 서버가 만든다.
 */
export function formatDay(iso: string): string {
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return "";
  return DATE_ONLY.format(at);
}

/** `Date` → "2026년 9월". 달력 머리에 쓴다. */
export function formatMonth(date: Date): string {
  return MONTH_ONLY.format(date);
}

/** `Date` → `YYYY-MM`. 월 조회 쿼리 파라미터다 (표시가 아니라 키). */
export function toMonthKey(date: Date): string {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}`;
}

/**
 * `Date` → `YYYY-MM-DD`. 🚨 `toISOString()` 은 UTC 라 시간대에 따라 **하루가 밀린다** —
 * 달력에서 고른 날과 서버에 보내는 날이 달라지는 종류의 사고다.
 */
export function toISODate(date: Date): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

/**
 * `YYYY-MM-DD` → `Date`(로컬 자정). 🚨 `new Date("2026-09-12")` 는 **UTC 자정**으로 읽어서
 * 한국에서는 같은 날이지만 시간대에 따라 전날이 된다 — 위 `toISODate` 와 짝이다.
 * 잘못된 값이면 `null` 이다. 화면이 조용히 오늘로 대체하지 않게 판단은 호출부가 한다.
 */
export function parseISODate(value: string): Date | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  if (!m) return null;
  const date = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
  return Number.isNaN(date.getTime()) ? null : date;
}

/**
 * 조사 "로 / 으로" 를 앞 글자의 받침으로 고른다. **표시 변환이다** — 날짜 계산과 달리
 * 규칙이 한글 코드포인트로 닫혀 있어서 서버에 물을 것이 없다.
 *
 * 🚨 **문구를 `라벨 + 조사` 로 조립하는 자리에만 쓴다.** 교정 판정 이름이 늘어날 때마다
 *    "잘못된 기록**로** 바꿀까요?" 같은 문장이 생기는데, 라벨은 데이터고 조사는 문법이라
 *    라벨 쪽에 조사를 박아 두면 다른 문장에서 못 쓴다.
 *
 * 받침이 없거나 `ㄹ` 이면 "로", 그 밖에는 "으로". 한글이 아닌 글자로 끝나면 "로" 로 둔다
 * (영문·숫자는 읽는 방식이 갈려서 규칙 하나로 정할 수 없다).
 */
export function withRo(word: string): string {
  const last = word.trim().at(-1);
  if (!last) return word;

  const code = last.charCodeAt(0);
  const isHangulSyllable = code >= 0xac00 && code <= 0xd7a3;
  if (!isHangulSyllable) return `${word}로`;

  const finalConsonant = (code - 0xac00) % 28;
  // 0 = 받침 없음, 8 = ㄹ
  return finalConsonant === 0 || finalConsonant === 8 ? `${word}로` : `${word}으로`;
}
