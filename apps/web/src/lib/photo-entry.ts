import type { PhotoEntry, SubmitEventBody } from "@/lib/api/types";
import { seoulDate, seoulTime, withSeoulDateTime } from "@/lib/event-draft";
import { formatDateTime, formatDayRange, formatEventTime, formatTimeOfDay } from "@/lib/format";
import { isPast, NO_REMINDER, type ReminderChoice } from "@/lib/reminder";

/**
 * 08 사진 — 읽어낸 항목 하나의 **날짜가 무엇인지** 가르는 규칙 (#723).
 *
 * 🚨 **`date` 는 시작일일 수도, 마감일일 수도 있다.** 서버가 `starts_at ?? due_at` 의 날짜를
 *    `date` 로 보내고(`apps/api/app/api/runs/photo_run.py` 의 `_entry_date`), 보호자가 그 날을 고치면
 *    같은 규칙으로 시작 또는 마감을 고친다(`apply_edits`). 화면이 이 둘을 같은 "언제" 로 부르면
 *    할 일의 **마감일이 일정의 시작일로** 들어간다 — 실제로 그렇게 들어가고 있었다.
 *
 * 🚨 **날짜를 계산하지 않는다.** 서버가 준 ISO 를 서울 날짜·시각 글자로 옮기고 비교만 한다
 *    (apps/web/CLAUDE.md §4 · `lib/event-draft.ts` 머리말).
 */

/**
 * `date` 가 무엇의 날인가. 서버 `_entry_date`(`starts_at ?? due_at`)를 그대로 따른다 —
 * 시작을 읽었으면 시작, 마감만 읽었으면 마감이다. 둘 다 없으면 서버가 고칠 쪽(`apply_edits`)을 따른다.
 *
 * 🚨 **일정 항목이어도 마감만 읽었으면 마감이다.** 서버는 "4월 30일까지" 처럼 시작 없는 "까지" 를
 *    마감으로 옮기고 "시작 날짜를 찾지 못했어요" 를 단다(`assemble.py`). 종류만 보고 시작으로 다루면
 *    그 마감일이 시작 칸에 미리 채워져, 고치지 않고 누르면 마감일에 시작하는 일정이 들어간다.
 * 🚨 **서버 `apply_edits` 도 이 순서로 고칠 칸을 정한다** (#723 PR) — 이 함수를 바꾸면 서버도 같이 바꾼다.
 *    어긋나면 화면이 "마감" 이라고 부른 칸을 고쳤는데 서버가 시작으로 저장한다.
 */
export function entryDateRole(entry: PhotoEntry): "start" | "due" {
  if (entry.starts_at) return "start";
  if (entry.due_at) return "due";
  return entry.kind === "event" ? "start" : "due";
}

/**
 * 위 날짜 칸의 이름. 🚨 **"마감" 은 일정으로 넣을 수 있는 항목의 마감일 때만이다** (#792 리뷰).
 *    급식은 `date` 만 오고 `starts_at` · `due_at` 이 없어서 `entryDateRole` 이 `"due"` 가 되고, 날짜 없는
 *    안내도 그렇다. 판정은 서버 `apply_edits` 와 같은 순서라 그대로 두고 **이름표만** 가린다 —
 *    급식 날짜를 "마감" 이라고 부르면 그날까지 내야 하는 것으로 읽힌다.
 */
export function entryDateLabel(entry: PhotoEntry): "마감" | "언제" {
  return canBecomeEvent(entry) && entryDateRole(entry) === "due" ? "마감" : "언제";
}

/**
 * 일정으로 넣을 수 있는 종류인가.
 *
 * 🚨 **급식은 아니다** — 식단표는 전부 날짜가 있지만 일정이 아니다.
 * 🚨 **안내도 아니다** (#723) — 날짜가 없는 것이 보통이라 일정 칸을 세우면 없는 날을 고르러
 *    들어가게 된다. 날짜가 있는 안내("휴원")를 일정으로 넣는 길은 다시 열 때 정한다.
 */
export function canBecomeEvent(entry: PhotoEntry): boolean {
  return entry.kind !== "meal" && entry.kind !== "info";
}

/**
 * 서버가 읽은 **여러 날 범위**. 시작과 끝이 다른 날일 때만 `{ from, to }`(`YYYY-MM-DD`)다.
 *
 * 🚨 **보호자가 시작일을 고쳤으면 범위가 없다.** 서버도 날짜를 바꾸면 그날 하루로 둔다
 *    (`apply_edits` — 고친 날에서 며칠 가는지는 알 수 없다). 고친 날 옆에 읽은 끝날을 그대로
 *    세우면 저장되지도 않을 범위를 말하게 된다.
 */
export function readSpan(
  entry: PhotoEntry,
  date: string | null = entry.date,
): { from: string; to: string } | null {
  if (entryDateRole(entry) !== "start") return null;
  const from = seoulDate(entry.starts_at ?? null);
  const to = seoulDate(entry.ends_at ?? null);
  if (from === "" || to === "" || from === to) return null;
  if (date !== from) return null;
  return { from, to };
}

/**
 * 서버가 **시각까지** 읽은 순간 (시작 또는 마감의 ISO). 시각을 못 읽었거나 하루 종일이면 `null` 이다.
 *
 * 🚨 **목록 줄이 이 시각을 오전 · 오후와 함께 보여준다** (#723 댓글). 서버는 "8시" 처럼 오전 · 오후가
 *    없는 시각을 오전으로 읽기로 했다 — 보호자가 일정 칸을 열지 않아도 그 판단을 볼 수 있어야 한다.
 * 🚨 **시작의 날짜가 `date` 와 다르면 `null`** 이다 — 날짜만 고친 읽은 값이 그렇다. 서버도 그런 시각은
 *    받지 않고 하루로 둔다(`apply_edits`). 시트의 시작 시간까지 얹은 값(`recordedEntry`)은 날짜가 맞는다.
 * 🚨 마감의 `all_day` 가 거짓이면 마감 시각을 읽은 것이다 — 시각이 없는 마감은 서버가 하루 종일로 보낸다.
 */
export function readMoment(entry: PhotoEntry): string | null {
  if (entry.all_day !== false || entry.date === null) return null;
  const at = (entryDateRole(entry) === "start" ? entry.starts_at : entry.due_at) ?? null;
  return at && seoulDate(at) === entry.date ? at : null;
}

/**
 * 시트가 처음 열릴 때의 시각 칸. 🚨 **읽은 시각이 있을 때만** 채운다 — 없는 것을 자정으로
 *    채우지 않는다 (`photo-entry-sheet.tsx` 의 시각 칸 주석 · #151).
 * 🚨 마감 항목에는 채우지 않는다. 그 시각은 끝나는 때지 시작이 아니다.
 */
export function readStartTime(entry: PhotoEntry): string {
  if (entryDateRole(entry) !== "start" || entry.all_day !== false) return "";
  return seoulTime(entry.starts_at ?? null);
}

/**
 * 캘린더에 넣을 때 싣는 끝 일시.
 *
 * 🚨 **읽은 그대로일 때만 싣는다** — 시작 날짜도, 하루 종일 여부도 읽은 값과 같고, 끝이 시작보다
 *    뒤일 때. 하나라도 바뀌면 `null`(그날 하루)이다. 고친 시작에 읽은 끝을 붙이면 끝이 시작보다
 *    앞서 서버가 400 으로 막거나(`SubmitEventBody._ends_after_starts`), 보호자가 고른 적 없는
 *    기간이 들어간다.
 */
export function carriedEndsAt(
  entry: PhotoEntry,
  pick: { date: string; allDay: boolean; startsAt: string | null },
): string | null {
  const ends = entry.ends_at ?? null;
  if (entryDateRole(entry) !== "start" || !ends || pick.startsAt === null) return null;
  if (pick.date !== seoulDate(entry.starts_at ?? null)) return null;
  if (pick.allDay !== (entry.all_day ?? true)) return null;
  const end = Date.parse(ends);
  const start = Date.parse(pick.startsAt);
  if (Number.isNaN(end) || Number.isNaN(start) || end <= start) return null;
  return ends;
}

/**
 * 읽은 끝을 **싣지 못하게 된** 까닭. 보호자가 시작을 고쳐서 끝이 빠졌을 때 시트가 한 줄 말한다 (#792 리뷰).
 *
 * - `"span"`: 여러 날 기간이 그날 하루로 줄었다
 * - `"time"`: 같은 날의 끝나는 시각이 빠졌다 (9:30 ~ 11:00 의 시작을 11:30 으로 고친 경우)
 *
 * 🚨 **하루 종일 하루짜리의 끝(그날 23:59)은 끝으로 치지 않는다** — 서버 관례일 뿐 보호자가 읽은 값이 아니다.
 * 🚨 시작이 아직 비었으면(`startsAt` 이 `null`) 말하지 않는다 — 잠긴 이유가 이미 버튼 위에 선다.
 */
export function droppedEnd(
  entry: PhotoEntry,
  pick: { startsAt: string | null; endsAt: string | null },
): "span" | "time" | null {
  if (pick.startsAt === null || pick.endsAt !== null) return null;
  if (readSpan(entry) !== null) return "span";
  if (entryDateRole(entry) !== "start" || entry.all_day !== false || !entry.ends_at) return null;
  return "time";
}

/* ── 캘린더에 넣기 (#824) ─────────────────────────────────────────────── */

/**
 * 항목을 캘린더에 넣을 때의 값. 고치기 시트의 "캘린더에 넣을 때" 칸이 고친다.
 *
 * 🚨 **넣는 것은 시트가 아니라 목록의 체크와 맨 아래 버튼이다** (#824). 한동안 시트 안에 게이트 버튼이
 *    있어서 일정이 셋이면 시트를 세 번 열었다. 그래도 값을 고치는 칸까지 빼면 할 일의 시작하는 날,
 *    못 읽은 시각, 알림을 고를 곳이 없어진다 — 그래서 값은 시트에, 넣는 일은 목록에 둔다.
 */
export interface EventPlan {
  /**
   * 마감 항목을 일정으로 넣을 때의 **시작 날짜.** 🚨 비어서 시작한다 — 마감일을 시작일로 채우면
   * "11/14 까지 회신" 이 11/14 에 시작하는 일정으로 들어간다 (#723). 시작 항목은 위 칸이 곧 시작이라 쓰지 않는다.
   */
  startDate: string;
  allDay: boolean;
  /** 🚨 읽은 시각이 있을 때만 채워서 시작한다 (`readStartTime`). 없는 것을 자정으로 채우지 않는다. */
  time: string;
  /** 🚨 기본값은 "없음" 이다 (#609). */
  reminder: ReminderChoice;
}

/**
 * 처음 값. 🚨 마감 항목의 `all_day` 는 **마감**이 하루 종일이라는 뜻이라(서버 `due_all_day`) 쓰지 않고
 * 하루 종일에서 시작한다.
 */
export function initialPlan(entry: PhotoEntry): EventPlan {
  return {
    startDate: "",
    allDay: entryDateRole(entry) === "start" ? (entry.all_day ?? true) : true,
    time: readStartTime(entry),
    reminder: NO_REMINDER,
  };
}

/** 일정이 들어갈 날. 시작 항목은 위 칸, 마감 항목은 따로 고른 시작하는 날이다. */
export function planDate(entry: PhotoEntry, plan: EventPlan): string {
  return entryDateRole(entry) === "start" ? (entry.date ?? "") : plan.startDate;
}

/** 일정의 시작 ISO. 날짜가 없거나, 하루 종일이 아닌데 시각이 없으면 `null` 이다. */
export function planStartsAt(entry: PhotoEntry, plan: EventPlan): string | null {
  return withSeoulDateTime(planDate(entry, plan), plan.time, plan.allDay);
}

/** 함께 넣는 끝. 읽은 그대로일 때만 실린다 (`carriedEndsAt`). */
export function planEndsAt(
  entry: PhotoEntry,
  plan: EventPlan,
  startsAt: string | null = planStartsAt(entry, plan),
): string | null {
  return carriedEndsAt(entry, { date: planDate(entry, plan), allDay: plan.allDay, startsAt });
}

/**
 * **저장되는 기록의 값.** 목록의 날짜 줄과 저장 요청(`commit`)이 이 값을 쓴다.
 *
 * 🚨 **보호자가 시트에서 고친 시작은 기록에도 들어간다.** 한동안 "시작 시간 · 하루 종일" 은 캘린더에
 *    넣는 값(`EventPlan`)만 바꿨다 — 저녁 8시를 오전 8시로 읽은 것을 고치면 캘린더에는 저녁 8시가
 *    들어가는데, 목록의 날짜 줄과 저장되는 기록에는 오전 8시가 남았다.
 * 🚨 **`entry` 자체를 덮어쓰지 않는다.** `entry` 는 사진에서 **읽은 값**이고, 읽은 끝을 함께 넣을지
 *    (`carriedEndsAt`) · 무엇이 빠졌다고 말할지(`droppedEnd`)가 그 읽은 값과 견준다. 덮어쓰면 한 번
 *    고친 뒤로는 고친 값이 읽은 값 행세를 한다. 그래서 보여줄 때와 보낼 때만 얹는다.
 * 🚨 **시작 항목만이다.** 마감 항목의 "시작하는 날 · 시작 시간" 은 캘린더에 넣을 때만 쓰는 값이고
 *    기록의 날짜는 마감이다. 급식 · 안내에는 그 칸이 없다.
 * 🚨 시작이 아직 정해지지 않았으면(하루 종일을 껐는데 시각이 빈 경우) 읽은 값 그대로다.
 * 🚨 **서버 `apply_edits` 가 같은 규칙으로 받는다** — 시작 항목에서만, `starts_at` 의 날짜가 `date` 와
 *    같을 때만. 끝은 읽은 끝 그대로일 때만 받는다 (`carriedEndsAt` 이 보내는 값이 그것뿐이다 · #952 리뷰).
 *    이 함수를 바꾸면 서버도 같이 본다.
 */
export function recordedEntry(entry: PhotoEntry, plan: EventPlan): PhotoEntry {
  if (!canBecomeEvent(entry) || entryDateRole(entry) !== "start") return entry;
  const startsAt = planStartsAt(entry, plan);
  if (startsAt === null) return entry;
  return {
    ...entry,
    starts_at: startsAt,
    ends_at: planEndsAt(entry, plan, startsAt),
    all_day: plan.allDay,
  };
}

/**
 * 처음부터 "캘린더에 넣기" 를 켜 두는가.
 *
 * 🚨 **시작 날짜를 읽은 일정만이다.** 할 일은 보호자가 고른다 — 회신 마감을 일정으로 넣을지는
 *    읽은 글자가 정하지 못한다. 마감만 읽은 일정("4월 30일까지")도 켜지 않는다. 그 날은 시작이 아니다.
 * 🚨 확인이 필요한 항목은 켜지 않는다. 저장이 그 항목을 받지 않아서, 일정만 들어가고 공지는 빠진다.
 * 🚨 **지난 일정은 켜지 않는다** (`past` · #828 리뷰). 지난주 알림장을 올리면 이미 지난 일정도 읽힌다.
 *    지났는지는 서버가 정한다 — 화면이 오늘과 견주지 않는다. 보호자가 켜면 넣을 수 있다.
 * 🚨 **켜 둔 것은 넣은 것이 아니다.** 넣는 것은 보호자가 맨 아래 버튼을 누를 때고, 그 버튼 바로 위에
 *    무엇이 들어가는지 목록이 선다 (`photo-review.tsx`). 접힌 목록 안에 켜진 것이 있어도 거기서 보인다.
 */
export function startsChecked(entry: PhotoEntry): boolean {
  return (
    entry.kind === "event" &&
    entryDateRole(entry) === "start" &&
    entry.date !== null &&
    !entry.needs_review &&
    !entry.past &&
    planLocks(entry, initialPlan(entry)).length === 0
  );
}

/**
 * 지난 일정이라는 것 **하나 때문에** 꺼 둔 줄인가 (#828 리뷰). 화면이 그 줄에 "지난 일정이라 꺼
 * 두었어요." 를 말할지를 정한다.
 * 🚨 `past` 만 보지 않는다. 할 일은 지나지 않았어도 켜지 않으니, 지난 할 일에 이 말을 붙이면 틀린
 *    까닭이다. `past` 를 빼면 켜졌을 줄만이다.
 */
export function offBecausePast(entry: PhotoEntry): boolean {
  return entry.past === true && startsChecked({ ...entry, past: false });
}

/**
 * 확인이 필요했던 항목을 보호자가 시트에서 **처음 확인한 순간** "캘린더에 넣기" 를 켜는가.
 *
 * 🚨 **처음부터 잘 읽은 일정(`startsChecked`)과 같은 조건이다.** 확인이 필요한 항목을 처음에 켜 두지 않는
 *    까닭은 하나였다 — 확인 전에는 저장이 그 항목을 받지 않는다. 확인하면 그 까닭이 없어진다.
 *    한동안 확인한 뒤에도 꺼 두었는데, 확인한 줄은 접힌 "잘 읽었어요" 로 옮겨 가서 꺼진 체크가 보이지 않았다.
 *    보호자는 넣었다고 믿고 저장했고 일정은 캘린더에 없었다.
 * 🚨 **넣는 값은 시트에서 확인한 값이다** (`plan`). `startsChecked` 는 읽은 값(`initialPlan`)으로 보지만
 *    여기서는 방금 보호자가 고른 날짜 · 시각으로 잠기는지 본다.
 * 🚨 **할 일은 켜지 않는다** — 회신 마감을 일정으로 넣을지는 보호자가 고른다 (`startsChecked` 와 같다).
 * 🚨 **켜는 것은 넣는 것이 아니다.** 넣는 것은 맨 아래 버튼이고, 그 위에 무엇이 들어가는지 목록이 선다.
 * 🚨 처음 확인할 때 한 번만 본다. 이미 확인한 줄을 다시 고칠 때는 켜지 않는다 — 보호자가 끈 것을
 *    되살리지 않는다 (`photo-review.tsx` 의 `saveEntry`).
 */
export function checkedOnConfirm(entry: PhotoEntry, plan: EventPlan): boolean {
  return (
    entry.kind === "event" &&
    entryDateRole(entry) === "start" &&
    entry.date !== null &&
    !entry.needs_review &&
    !entry.past &&
    planLocks(entry, plan).length === 0
  );
}

/**
 * 지금 넣을 수 없는 까닭. 비어 있으면 넣을 수 있다.
 *
 * 🚨 **막는 것이 여럿이면 전부 말한다** (초안 카드와 같은 규칙). 하나씩 말하면 보호자가 날짜를 채운 뒤에야
 *    두 번째 관문을 알게 된다.
 * 🚨 확인이 필요한 항목은 그 하나만 말한다 — 확인하고 나면 나머지 값이 바뀌어 있을 수 있다.
 */
export function planLocks(entry: PhotoEntry, plan: EventPlan, now = Date.now()): string[] {
  if (entry.needs_review) return ["고치기에서 확인하면 넣을 수 있어요."];
  const startsAt = planStartsAt(entry, plan);
  return [
    planDate(entry, plan) === ""
      ? entryDateRole(entry) === "start"
        ? "고치기에서 날짜를 고르면 넣을 수 있어요."
        : "고치기에서 시작하는 날을 고르면 넣을 수 있어요."
      : null,
    // 🚨 하루 종일이면 시각을 안 묻는다 — 물을 것이 없는 것이지 안 고른 것이 아니다.
    !plan.allDay && plan.time === "" ? "고치기에서 시작 시간을 고르면 넣을 수 있어요." : null,
    // 🚨 조용히 "없음" 으로 바꾸지 않는다 — 고른 것과 들어간 것이 달라진다 (초안 카드와 같다).
    plan.reminder !== NO_REMINDER &&
    startsAt !== null &&
    isPast(plan.reminder, startsAt, plan.allDay, now)
      ? "고른 알림 시각이 이미 지났어요. 고치기에서 알림을 다시 골라 주세요."
      : null,
  ].filter((reason) => reason !== null);
}

/**
 * 캘린더에 넣는 본문. 넣을 수 없으면 `null` 이다.
 * 🚨 알림장에서 읽은 것은 기관이 알려준 일정이다 (계약서 §09 와 같은 출처 판정).
 */
export function eventBody(entry: PhotoEntry, plan: EventPlan): SubmitEventBody | null {
  const startsAt = planStartsAt(entry, plan);
  if (startsAt === null) return null;
  return {
    event: {
      title: entry.title.trim(),
      starts_at: startsAt,
      ends_at: planEndsAt(entry, plan, startsAt),
      all_day: plan.allDay,
      event_type: "episodic",
      category: "institution",
    },
    items: entry.items.map((item) => ({ item_id: null, item_name: item })),
    reminder: plan.reminder === NO_REMINDER ? null : { lead: plan.reminder },
  };
}

/** 캘린더에 넣은 것. 🚨 일시는 **응답에서** 받는다 — 같은 키 재시도면 서버는 처음 넣은 일정을 돌려준다 (#792 리뷰). */
export interface AddedEvent {
  /** 🚨 저장할 때 그 항목의 `event_id` 로 실어 보낸다 — 그래야 서버가 일정에 공지 항목을 출처로 단다. */
  event_id: string;
  starts_at: string;
  ends_at: string | null;
  all_day: boolean;
}

/**
 * 일정 한 줄의 일시. 여러 날이면 기간, 같은 날 끝나는 시각이 있으면 그것까지 쓴다.
 * 🚨 하루 종일 하루짜리의 끝(그날 23:59)은 말하지 않는다 — 서버 관례일 뿐 보호자가 고른 값이 아니다.
 */
export function eventWhen(startsAt: string, endsAt: string | null, allDay: boolean): string {
  const from = seoulDate(startsAt);
  const to = seoulDate(endsAt);
  if (to !== "" && to !== from) return formatDayRange(from, to);
  if (!allDay && endsAt !== null && to === from) {
    return `${formatDateTime(startsAt)}부터 ${formatTimeOfDay(endsAt)}까지`;
  }
  return formatEventTime(startsAt, allDay);
}
