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

/**
 * 🚨 **연도가 있고 요일이 없다.** `DATE_ONLY` 는 최근 날짜를 훑는 자리(측정 기록 · 캘린더)용이라
 *    연도를 빼고 요일을 넣었는데, **생일에는 정확히 반대가 필요하다** — 어느 해에 태어났는지가
 *    본체이고, 2021년 4월 2일이 금요일이었다는 것은 아무도 안 궁금하다.
 *    한동안 생일에 `DATE_ONLY` 를 썼더니 화면에 "4월 2일 (금)" 이 떴다.
 */
const DATE_WITH_YEAR = new Intl.DateTimeFormat("ko-KR", {
  timeZone: TIME_ZONE,
  year: "numeric",
  month: "long",
  day: "numeric",
});

/**
 * 요일도 해도 없는 날짜 ("9월 12일". `DATE_ONLY` 와 달리 요일이 빠진다).
 * 🚨 **축 눈금 전용이다.** 요일은 훑는 목록에서 쓸모가 있지만 그래프 가로축에서는 글자만
 *    늘려서 눈금끼리 겹친다 — 겹친 눈금은 없는 눈금과 같다.
 */
const MONTH_DAY = new Intl.DateTimeFormat("ko-KR", {
  timeZone: TIME_ZONE,
  month: "long",
  day: "numeric",
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
 * 시각만 ("오전 10:00"). 🚨 **날짜를 이미 다른 칸이 말하고 있을 때** 쓴다 —
 * 일정 초안 카드는 날짜를 `DateField` 가 지고 있어서, 여기서 `formatEventTime` 을 쓰면
 * 같은 날짜가 두 가지 표기로 두 번 선다 ("2026년 9월 18일" 바로 아래 "9월 18일 (금) 오전 10:00").
 */
const TIME_ONLY = new Intl.DateTimeFormat("ko-KR", {
  timeZone: TIME_ZONE,
  hour: "numeric",
  minute: "2-digit",
});

export function formatTimeOfDay(iso: string): string {
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return "";
  return TIME_ONLY.format(at);
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

/**
 * 생일처럼 **해가 중요한 날짜** 한 줄 ("2021년 4월 2일"). `YYYY-MM-DD` 와 ISO 시각을 모두 받는다.
 * 🚨 나이를 만들지 않는다 — `age_display` 는 서버 문구다 (apps/web/CLAUDE.md §4).
 */
export function formatDateWithYear(iso: string): string {
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return "";
  return DATE_WITH_YEAR.format(at);
}

/**
 * 날짜 하나를 요일 없이 ("9월 12일"). `YYYY-MM-DD` 와 밀리초 타임스탬프를 모두 받는다.
 * 🚨 상대 표현("3일 전")을 만들지 않는다 — 그건 `measured_label` 이고 서버가 만든다.
 */
export function formatMonthDay(value: string | number): string {
  const at = new Date(value);
  if (Number.isNaN(at.getTime())) return "";
  return MONTH_DAY.format(at);
}

/**
 * 그래프 가로축의 눈금 글자를 만드는 함수를 돌려준다. **축 전체의 범위를 보고** 형식을 고른다.
 *
 * 🚨 **해가 바뀌는 축에서 "9월 13일 … 9월 8일" 이 나오면 축이 거꾸로 읽힌다.** 왼쪽이
 *    작년 9월, 오른쪽이 올해 9월인데 글자만 보면 뒤로 간 것처럼 보인다 — 실제로 그렇게 찍혔다.
 *    그래서 두 해에 걸친 축에서는 **해를 붙이고 날은 뺀다**("2025년 9월"). 축 끝에 15글자짜리
 *    날짜가 서면 세로축 숫자 위로 넘어오고, 정확한 날짜는 어차피 아래 목록과 점 설명이 진다.
 */
export function axisDayFormatter(from: string | number, to: string | number) {
  const a = new Date(from);
  const b = new Date(to);
  const crossesYear =
    !Number.isNaN(a.getTime()) && !Number.isNaN(b.getTime()) && a.getFullYear() !== b.getFullYear();

  return (value: string | number): string => {
    const at = new Date(value);
    if (Number.isNaN(at.getTime())) return "";
    return crossesYear ? MONTH_ONLY.format(at) : MONTH_DAY.format(at);
  };
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
