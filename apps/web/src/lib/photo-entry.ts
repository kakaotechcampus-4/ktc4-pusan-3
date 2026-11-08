import type { PhotoEntry } from "@/lib/api/types";
import { seoulDate, seoulTime } from "@/lib/event-draft";

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
 * 🚨 **보호자가 날짜를 고쳤으면 `null`** 이다. 서버가 날짜를 고치면 시각을 버리고 하루로 둔다(`apply_edits`).
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
