import type {
  AnyEventDraft,
  EventDraftFields,
  EventRepeat,
  RepeatRequest,
  SeriesDraft,
  SeriesDraftFields,
  UpdateSeriesBody,
} from "@/lib/api/types";
import { formatTimeOfDay } from "@/lib/format";

/**
 * 일정 초안의 **일시 조립.**
 *
 * 🚨 **날짜를 계산하는 것이 아니라 조립하는 것이다.** 보호자가 달력·시각 칸에서 고른 값(또는
 *    사진에서 읽어낸 값)을 ISO 로 옮겨 담을 뿐이고, 나이·기간·상대 시간은 여전히 만들지 않는다
 *    (CLAUDE.md §3 · `lib/format.ts` 머리말). 제출받는 쪽이 `check_when` 으로 다시 검증한다 (#122).
 *
 * 🚨 **모르는 값을 채우지 않는다.** 날짜든 시각이든 안 고른 것은 `null` 로 남고, 그 상태에서는
 *    제출이 잠긴다. 한동안 시각이 없으면 **자정으로 접어** 넣었는데, 그러면 아무도 고르지 않은
 *    "오전 12:00" 이 승인 게이트를 지나 캘린더에 들어간다 (`docs/web/event-draft-ui-v1.md` §3-4
 *    "오늘로 기본값을 넣지 않는다" 가 시각에서 새고 있던 자리다 · #151).
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

const SEOUL_HM = new Intl.DateTimeFormat("en-GB", {
  timeZone: "Asia/Seoul",
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
});

/** ISO 시각 → 서울 기준 `YYYY-MM-DD`. 🚨 값이 없으면 **빈 문자열**이다 — 오늘로 채우지 않는다. */
export function seoulDate(iso: string | null): string {
  if (!iso) return "";
  const at = new Date(iso);
  return Number.isNaN(at.getTime()) ? "" : SEOUL_YMD.format(at);
}

/** ISO 시각 → 서울 기준 `HH:MM`. 🚨 값이 없으면 **빈 문자열**이다 — 자정으로 채우지 않는다. */
export function seoulTime(iso: string | null): string {
  if (!iso) return "";
  const at = new Date(iso);
  return Number.isNaN(at.getTime()) ? "" : SEOUL_HM.format(at);
}

/**
 * 고른 날짜와 시각을 ISO 한 개로 — 🚨 **둘 중 하나라도 비면 `null`** 이다.
 *
 * 🚨 "하루 종일" 은 시각을 **안 묻는 것**이지 자정을 고른 것이 아니다. 그래서 그때만 자정을
 *    쓰고, `all_day: true` 가 그 사실을 함께 나른다 — 시각이 비어서 자정이 된 것과 다르다.
 */
export function withSeoulDateTime(date: string, time: string, allDay: boolean): string | null {
  if (date === "") return null;
  if (allDay) return `${date}T00:00:00${SEOUL_OFFSET}`;
  if (time === "") return null;
  return `${date}T${time}:00${SEOUL_OFFSET}`;
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
 * 시작을 고른 값으로 옮긴다.
 *
 * 🚨 **`ends_at` 을 두고 가지 않는다** — `starts_at` 만 옮기면 끝이 시작보다 앞선 일정이
 *    제출된다. 걸린 시간(`ends_at - starts_at`)을 유지한 채로 같이 옮긴다.
 * 🚨 **시작이 사라지면 끝도 사라진다.** 끝만 남은 일정은 말이 안 된다 — 제출이 잠겨 있어서
 *    지금은 안 나가지만, 잠금이 풀리는 날 조용히 나가는 쪽으로 두지 않는다.
 */
export function moveStart(
  event: EventDraftFields,
  starts: string | null,
): Partial<EventDraftFields> {
  if (!starts) return { starts_at: null, ends_at: null };
  if (!event.ends_at || !event.starts_at) return { starts_at: starts };

  const span = Date.parse(event.ends_at) - Date.parse(event.starts_at);
  if (Number.isNaN(span)) return { starts_at: starts };
  return { starts_at: starts, ends_at: new Date(Date.parse(starts) + span).toISOString() };
}

/* ── 반복 일정 (#738) ──────────────────────────────────────────────── */

/** 반복 묶음 전체를 고치는 초안인가. 🚨 일정 카드의 모양이 아니라서 그리기 전에 반드시 가른다. */
export function isSeriesDraft(draft: AnyEventDraft): draft is SeriesDraft {
  return draft.op === "update_series";
}

/**
 * 초안이 고치는 대상. 🚨 **같은 대상의 초안은 최신 한 장만 남긴다** (`stores/event-draft.ts` 의 `mergeDrafts`) —
 * 낡은 장을 나중에 내면 새 장의 준비물이 지워진다. 새 일정은 대상이 없어 `null` 이다.
 * 🚨 일정과 묶음의 id 가 겹쳐도 다른 대상이라 앞에 종류를 붙인다.
 */
export function draftTarget(draft: AnyEventDraft): string | null {
  if (isSeriesDraft(draft)) return `series:${draft.series_id}`;
  return draft.event_id === null ? null : `event:${draft.event_id}`;
}

/** 제출에 싣는 반복 규칙. 🚨 `label` 은 화면에 그리는 서버 문구라 되돌려 보내지 않는다. */
export function repeatRequest(repeat: EventRepeat): RepeatRequest {
  return { weekdays: repeat.weekdays, until: repeat.until };
}

/**
 * 묶음 고치기 제출 본문. 🚨 **카드가 읽기 전용이라 초안 값을 그대로 보낸다** — `before` 까지 실어야 서버가
 * 달라진 값만 아직 시작하지 않은 회차에 적용한다 (`UpdateSeriesBody`).
 */
export function seriesSubmitBody(draft: SeriesDraft): UpdateSeriesBody {
  return {
    event: draft.event,
    repeat: repeatRequest(draft.repeat),
    before: draft.before ? { ...draft.before, repeat: repeatRequest(draft.before.repeat) } : null,
    items: draft.items,
  };
}

/**
 * 묶음의 하루 시각 한 줄 ("오전 9:00", 종일이면 "하루 종일"). 🚨 **날짜를 계산하는 것이 아니라 표기만 바꾼다** —
 * 서버가 준 KST `HH:MM` 을 아무 날에나 붙여 시각 부분만 읽는다. 날짜는 화면에 나오지 않는다.
 */
export function seriesTimeLabel(fields: SeriesDraftFields): string {
  if (fields.all_day) return "하루 종일";
  if (!fields.starts_time) return "시각 없음";
  const start = formatClock(fields.starts_time);
  return fields.ends_time ? `${start} ~ ${formatClock(fields.ends_time)}` : start;
}

function formatClock(hhmm: string): string {
  return formatTimeOfDay(`2000-01-01T${hhmm}:00${SEOUL_OFFSET}`) || hhmm;
}
