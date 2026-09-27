import type { EventDraftFields } from "@/lib/api/types";

/**
 * 일정 초안의 **날짜 조립.**
 *
 * 🚨 **날짜를 계산하는 것이 아니라 조립하는 것이다.** 보호자가 달력에서 고른 날(또는 사진에서
 *    읽어낸 날)을 ISO 로 옮겨 담을 뿐이고, 나이·기간·상대 시간은 여전히 만들지 않는다
 *    (CLAUDE.md §3 · `lib/format.ts` 머리말). 제출받는 쪽이 `check_when` 으로 다시 검증한다 (#122).
 *
 * ⚠️ 시간대를 기기 설정이 아니라 `Asia/Seoul` 로 고정한다. 승인 게이트 앞에서 기기 시간대가
 *    다르면 확인한 날과 들어간 날이 달라진다 (`format.ts` 와 같은 이유).
 *
 * 🚨 **여기 있는 이유** — 초안 카드(04·05·08)와 사진 고치기 시트가 **같은 조립을 한다.**
 *    양쪽에 따로 두면 한쪽만 고쳐져 같은 날이 다른 ISO 로 나간다.
 */
const SEOUL_OFFSET = "+09:00";

const SEOUL_YMD = new Intl.DateTimeFormat("en-CA", {
  timeZone: "Asia/Seoul",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
});

const SEOUL_HMS = new Intl.DateTimeFormat("en-GB", {
  timeZone: "Asia/Seoul",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hourCycle: "h23",
});

/** ISO 시각 → 서울 기준 `YYYY-MM-DD`. 🚨 값이 없으면 **빈 문자열**이다 — 오늘로 채우지 않는다. */
export function seoulDate(iso: string | null): string {
  if (!iso) return "";
  const at = new Date(iso);
  return Number.isNaN(at.getTime()) ? "" : SEOUL_YMD.format(at);
}

/** 고른 날짜를 원래 시각에 얹는다. 시각을 모르거나 하루 종일이면 자정이다. */
export function withSeoulDate(iso: string | null, date: string, allDay: boolean): string | null {
  if (date === "") return null;
  const at = iso ? new Date(iso) : null;
  const time = !allDay && at && !Number.isNaN(at.getTime()) ? SEOUL_HMS.format(at) : "00:00:00";
  return `${date}T${time}${SEOUL_OFFSET}`;
}

/** 두 ISO 시각이 같은 순간인가. 표기가 달라도 같으면 참이다. */
export function sameInstant(a: string | null, b: string | null): boolean {
  if (a === b) return true;
  if (!a || !b) return false;
  const left = Date.parse(a);
  const right = Date.parse(b);
  return !Number.isNaN(left) && !Number.isNaN(right) && left === right;
}

/**
 * 고른 날짜로 옮긴다. 🚨 **`ends_at` 을 두고 가지 않는다** — `starts_at` 만 옮기면 끝이 시작보다
 *    앞선 일정이 제출된다. 걸린 시간(`ends_at - starts_at`)을 유지한 채로 같이 옮긴다.
 */
export function moveToDate(
  event: EventDraftFields,
  date: string,
  originalStartsAt: string | null,
): Partial<EventDraftFields> {
  const starts = withSeoulDate(
    event.all_day ? event.starts_at : originalStartsAt,
    date,
    event.all_day,
  );
  if (!event.ends_at || !event.starts_at || !starts) return { starts_at: starts };

  const span = Date.parse(event.ends_at) - Date.parse(event.starts_at);
  if (Number.isNaN(span)) return { starts_at: starts };
  return { starts_at: starts, ends_at: new Date(Date.parse(starts) + span).toISOString() };
}
