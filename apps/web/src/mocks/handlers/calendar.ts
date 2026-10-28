import { http, HttpResponse } from "msw";

import {
  DIARY_CONTENT_MAX_LENGTH,
  type CalendarDay,
  type CalendarDayResponse,
  type CalendarMonthResponse,
  type EventItem,
  type EventItemUpdateResponse,
  type MyDiary,
  type Observation,
  type Reminder,
  type ReminderLead,
  type ReminderRequest,
  type ReminderResponse,
} from "@/lib/api/types";
import { fitsKind, forKind, isPast, NO_REMINDER } from "@/lib/reminder";

import {
  calendarEventDates,
  calendarEventsOn,
  confirmedEvent,
  deleteMyDiary,
  myDiaryDates,
  readMyDiary,
  sharedPhotoDates,
  sharedPhotosOn,
  writeMyDiary,
} from "../fixtures";
import { currentScenario } from "../scenario";
import { chatCountOn } from "./chat";
import { apiError, networkDelay, url } from "./helpers";
import { withIdempotency } from "./idempotency";
import { listedObservations } from "./memories";

/**
 * 09 캘린더 핸들러. 서버(#318 1/3 · PR #325 달 보기 · 하루 보기, 2/3 · PR #333 일기 쓰기 · 지우기,
 * 3/3 · PR #335 준비물 체크)와 같은 모양을 내린다.
 *
 * 🚨 **일기는 관찰과 별도 저장소다** (계약서 §09). 목에서도 일기를 쓴다고 관찰이 늘지 않는다 —
 *    "일기는 관찰로 자동 추출하지 않는다" 를 화면이 지키는지 확인하려면 목이 먼저 지켜야 한다.
 *
 * 🚨 **일기와 사진도 서로 다른 저장소다** (#115). 일기는 쓴 보호자 본인 것만, 사진은 연결된 보호자가
 *    함께 본다.
 *
 * 🚨 **기록은 기록 탭과 같은 기준이다** (`listedObservations`). 달 보기의 숫자와 하루 보기의 목록도
 *    같은 함수에서 나온다 — 어긋나면 "기록 3건" 을 눌렀는데 2건이 나온다.
 */

/**
 * 준비물 체크 상태. 🚨 프로세스 수명만큼 산다 — 테스트는 `resetCalendarState()` 로 되돌린다.
 *
 * 🚨 **체크 시각도 같이 든다.** 서버는 이미 체크된 것을 다시 체크하면 처음 시각을 그대로 둔다
 *    (`set_event_item_prepared` · PR #335). 체크 여부만 들면 하루 보기가 픽스처 시각을 그대로 내려서
 *    PATCH 응답과 다시 읽은 값이 어긋난다.
 */
type PreparedState = Pick<EventItem, "is_prepared" | "prepared_at">;

const preparedItems = new Map<string, PreparedState>();

/**
 * 내 일정 알림 (#594 · #609). 🚨 프로세스 수명만큼 산다 — `resetCalendarState()` 로 되돌린다.
 *
 * 🚨 **목에는 보호자가 한 명이라 "내 것" 만 든다.** 실서버는 보호자마다 따로 들고, 응답에는 요청한
 *    보호자의 것만 싣는다 — 다른 보호자의 알림이 섞이는 경우는 목으로 만들 수 없다.
 */
const myReminders = new Map<string, Reminder>();

export function resetCalendarState(): void {
  myReminders.clear();
  preparedItems.clear();
  for (const { item_id, is_prepared, prepared_at } of confirmedEvent.items) {
    preparedItems.set(item_id, { is_prepared, prepared_at });
  }
}
resetCalendarState();

function withPrepared(date: string) {
  return calendarEventsOn(date).map((event) => ({
    ...event,
    items: event.items.map((item) => ({ ...item, ...preparedItems.get(item.item_id) })),
    reminder: myReminders.get(event.id) ?? null,
  }));
}

const REMINDER_LEADS: readonly string[] = [
  "at_start",
  "5m",
  "10m",
  "15m",
  "30m",
  "1h",
  "2h",
  "1d",
  "2d",
  "1w",
  "day_of_9am",
  "1d_9am",
  "2d_9am",
  "1w_9am",
] satisfies ReminderLead[];

/** 계약의 알림 값인가. 목록 밖이면 서버는 바디 검증에서 400 이다. */
export function isReminderLead(value: unknown): value is ReminderLead {
  return typeof value === "string" && REMINDER_LEADS.includes(value);
}

/**
 * 서버 `check_reminder` 와 같다 — 그 일정 종류에 맞지 않거나 울릴 시각이 지났으면 422.
 * 🚨 서버는 같은 시각도 지난 것으로 본다 (`<=`).
 */
export function refuseReminder(lead: ReminderLead, startsAt: string, allDay: boolean) {
  if (!fitsKind(lead, allDay)) {
    return apiError(422, "invalid_reminder", "이 일정에는 걸 수 없는 알림이에요");
  }
  if (isPast(lead, startsAt, allDay)) {
    return apiError(422, "invalid_reminder", "알림 시각이 이미 지났어요");
  }
  return null;
}

/** 서버처럼 기간(`observed_from` ~ `observed_to`)이 그날과 겹치는 기록. 여러 날 기록은 걸친 날마다 나온다. */
function observationsOn(date: string): Observation[] {
  return listedObservations().filter((o) => o.observed_from <= date && date <= o.observed_to);
}

/**
 * 경로의 날짜가 실제 있는 날인지 (`YYYY-MM-DD`). 서버는 `dt.date` 로 받아서 형식 오류 · 없는 날
 * (`2월 30일`) · 계산 한계 밖(1년 · 9999년 · `calendar_dates.py`)을 경로 오류 422 로 낸다.
 */
function isCalendarDate(date: string): boolean {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) return false;
  const year = Number(date.slice(0, 4));
  if (year < 2 || year > 9998) return false;
  const day = new Date(`${date}T00:00:00Z`);
  return !Number.isNaN(day.getTime()) && day.toISOString().slice(0, 10) === date;
}

/** `from` ~ `to` 의 날짜 전부 (YYYY-MM-DD). 서버 대신 날짜를 세는 자리다 — 화면 코드에는 두지 않는다. */
function datesBetween(from: string, to: string): string[] {
  const dates: string[] = [];
  for (let day = new Date(`${from}T00:00:00Z`); ; day.setUTCDate(day.getUTCDate() + 1)) {
    const date = day.toISOString().slice(0, 10);
    if (date > to) return dates;
    dates.push(date);
  }
}

/** 🚨 표시할 것이 하나라도 있는 날만, 날짜순. 빈 날은 싣지 않는다 (서버와 같다). */
function calendarMonth(month: string): CalendarDay[] {
  const dates = new Set<string>();
  for (const o of listedObservations()) {
    for (const date of datesBetween(o.observed_from, o.observed_to)) dates.add(date);
  }
  for (const date of calendarEventDates()) dates.add(date);
  for (const date of myDiaryDates()) dates.add(date);
  for (const date of sharedPhotoDates()) dates.add(date);

  return [...dates]
    .filter((date) => date.startsWith(month))
    .sort()
    .map((date) => ({
      date,
      has_event: calendarEventsOn(date).length > 0,
      has_my_diary: readMyDiary(date) !== null,
      has_shared_photo: sharedPhotosOn(date).length > 0,
      observation_count: observationsOn(date).length,
    }))
    .filter(
      (day) => day.has_event || day.has_my_diary || day.has_shared_photo || day.observation_count,
    );
}

function calendarDay(childId: string, date: string): CalendarDayResponse {
  return {
    my_diary: readMyDiary(date),
    shared_photos: sharedPhotosOn(date),
    events: withPrepared(date),
    observations: observationsOn(date),
    // 🚨 지난 대화 목록과 같은 표에서 센다 (`handlers/chat.ts`) — 내 대화만 센다.
    chat_count: chatCountOn(childId, date),
  };
}

/**
 * 서버 `DiaryRequest` 의 길이 · NUL 검사와 같다 (#549 · 서버 PR #550).
 *
 * 🚨 **앞뒤 공백을 떼지 않고 센다** — 신고(`reports.ts`)와 다르다. 일기는 원문 그대로 저장한다.
 * 🚨 **서버 바디 검증이 라우터의 공백 검사보다 먼저다.** 그래서 공백뿐이어도 1000자를 넘으면 422 가 아니라 400 이다.
 * 서버(Python)는 글자를 코드 포인트로 센다. JS `length` 는 UTF-16 단위라 이모지를 2 로 센다.
 */
function isDiaryContentAccepted(content: string): boolean {
  return [...content].length <= DIARY_CONTENT_MAX_LENGTH && !content.includes("\u0000");
}

export const calendarHandlers = [
  http.get(url("/children/:cid/calendar"), async ({ request }) => {
    await networkDelay();
    const month = new URL(request.url).searchParams.get("month");
    if (!month) return apiError(422, "validation_failed", "month 가 필요해요");
    // 서버처럼 쿼리의 형식 오류는 400 이다 (#325 · errors.py).
    if (!/^\d{4}-(0[1-9]|1[0-2])$/.test(month)) {
      return apiError(400, "validation_failed", "날짜가 올바르지 않아요");
    }

    const body: CalendarMonthResponse = {
      days: currentScenario() === "empty" ? [] : calendarMonth(month),
    };
    return HttpResponse.json(body);
  }),

  http.get(url("/children/:cid/calendar/:date"), async ({ params }) => {
    await networkDelay();
    const date = String(params.date);

    if (currentScenario() === "empty") {
      const empty: CalendarDayResponse = {
        my_diary: null,
        shared_photos: [],
        events: [],
        observations: [],
        chat_count: 0,
      };
      return HttpResponse.json(empty);
    }

    return HttpResponse.json(calendarDay(String(params.cid), date));
  }),

  /**
   * 그날 내 일기 쓰기 — 서버 `PUT /children/{cid}/diary/{date}` (#318 2/3 · PR #333) 와 같다.
   * 같은 날 다시 쓰면 덮어쓰고, 응답은 하루 보기 `my_diary` 와 같은 `MyDiary` 다.
   *
   * 🚨 **공백뿐이면 422 이고 기존 일기는 남는다.** 빈 내용은 지우기가 아니다 — 지우기는 아래
   *    `DELETE` 하나다. 저장은 앞뒤 공백 · 줄바꿈까지 원문 그대로다.
   * 🚨 **`content` 가 없거나 문자열이 아니면 400 이다** (서버 errors.py 의 바디 검증 — 경로는 422).
   *    1000자를 넘거나 NUL 이 들어도 400 이다 (#549 · `isDiaryContentAccepted`).
   * 🚨 **`withIdempotency` 로 감싸지 않는다.** 서버가 키를 받지 않는다 — 몇 번 보내도 결과가 같다.
   * 🚨 사진은 일기 밖에 있어서 여기서 건드리지 않는다. 일기를 덮어도 사진은 그대로다 (#115).
   */
  http.put(url("/children/:cid/diary/:date"), async ({ params, request }) => {
    await networkDelay();
    const date = String(params.date);
    if (!isCalendarDate(date)) return apiError(422, "validation_failed", "날짜가 올바르지 않아요");

    const body: unknown = await request.json().catch(() => null);
    const content =
      typeof body === "object" && body !== null ? (body as Record<string, unknown>).content : null;
    if (typeof content !== "string" || !isDiaryContentAccepted(content)) {
      return apiError(400, "validation_failed", "요청 형식이 올바르지 않아요", {
        fields: ["content"],
      });
    }
    if (content.trim() === "") {
      return apiError(422, "validation_failed", "일기 내용을 적어 주세요");
    }

    const saved: MyDiary = writeMyDiary(date, content);
    return HttpResponse.json(saved);
  }),

  /**
   * 그날 내 일기 지우기 — 서버 `DELETE /children/{cid}/diary/{date}` (PR #333) 와 같다.
   *
   * 🚨 **없는 일기를 지워도 204 다.** 두 번 눌러도 결과가 같은 요청이라 키도 받지 않는다.
   * 🚨 사진은 그대로다 (`deleteMyDiary` 주석 · #115).
   */
  http.delete(url("/children/:cid/diary/:date"), async ({ params }) => {
    await networkDelay();
    const date = String(params.date);
    if (!isCalendarDate(date)) return apiError(422, "validation_failed", "날짜가 올바르지 않아요");

    deleteMyDiary(date);
    return new HttpResponse(null, { status: 204 });
  }),

  /**
   * 🚨 **수정 초안의 제출. 승인 게이트 ㉠ 이다** — create 는 `POST`, update 는 `PATCH` 로
   *    갈린다 (9/21 회의). 경로는 서버 구현(#284)을 따른다 — 아이 스코프 아래다 (#316).
   *
   * 🚨 **키를 요구한다.** `item_id: null` 인 새 준비물은 멱등이 아니라서, 재시도가 같은 null 행을
   *    다시 보내면 준비물이 두 줄 들어간다 (`lib/api/operations.ts` 의 주석).
   *
   * 🚨 **`items` 에서 빠진 `item_id` 는 삭제다.** 보낸 것만 남는 것이 계약이고 (#122),
   *    보호자가 승인 화면에서 준비물을 지울 수 있는 유일한 표현 방법이다.
   */
  http.patch(
    url("/children/:cid/events/:eid"),
    withIdempotency(async ({ params, request }) => {
      await networkDelay();
      const eid = String(params.eid);
      const body = (await request.json()) as {
        event: { title: string; starts_at: string | null; all_day: boolean };
        items: Array<{ item_id: string | null; item_name: string }>;
      };

      // 화면이 막지만 계약도 막는다 — 규칙은 코드가 진다 (최상위 §3).
      if (!body.event.starts_at) {
        return apiError(400, "invalid_request", "일자가 없는 일정은 만들 수 없어요");
      }

      /**
       * 🚨 **`reminder` 칸이 없으면 걸린 알림을 그대로 둔다** (서버 #603). 웹은 수정에 이 칸을 싣지 않는다.
       *    걸린 알림은 서버 `_follow_event` 처럼 일정을 따라간다.
       *    - 시작도 종일 여부도 그대로면 건드리지 않는다
       *    - 종일 여부가 바뀌면 날 수를 지켜 옮기고, 옮긴 값이 이미 지났으면 그 알림만 지운다
       *    - 시작만 바뀌면 값은 그대로다. 지난 시각이 됐어도 지우지 않는다
       *    목은 고친 일정을 들고 있지 않아서 고치기 전 값은 늘 `confirmedEvent` 다.
       */
      const mine = myReminders.get(eid);
      const kindChanged = body.event.all_day !== confirmedEvent.all_day;
      const startChanged =
        Date.parse(body.event.starts_at) !== Date.parse(confirmedEvent.starts_at);
      if (mine && (kindChanged || startChanged)) {
        const moved = forKind(mine.lead, body.event.all_day);
        if (moved !== NO_REMINDER && !isPast(moved, body.event.starts_at, body.event.all_day)) {
          myReminders.set(eid, { lead: moved });
        } else if (kindChanged) {
          myReminders.delete(eid);
        }
      }

      return HttpResponse.json({
        event: {
          ...confirmedEvent,
          id: eid,
          title: body.event.title,
          starts_at: body.event.starts_at,
          all_day: body.event.all_day,
          reminder: myReminders.get(eid) ?? null,
          items: body.items.map((item, index) => ({
            item_id: item.item_id ?? `i_new_${index}`,
            item_name: item.item_name,
            // 🚨 초안은 체크 상태를 정하지 않는다 — 기존 행은 서버가 들고 있던 값을 지킨다 (#122 §4-3).
            ...((item.item_id && preparedItems.get(item.item_id)) || {
              is_prepared: false,
              prepared_at: null,
            }),
          })),
        },
      });
    }),
  ),

  /**
   * 내 알림 걸기 · 바꾸기 — 서버 `PUT /children/{cid}/events/{eid}/reminder` (#594 · PR #603) 와 같다.
   *
   * 🚨 **`withIdempotency` 로 감싸지 않는다.** 서버가 키를 받지 않는다 — 최종 상태를 보내는 요청이다.
   * 🚨 그 아이의 일정이 아니면 404, 값이 목록 밖이면 400(바디 검증), 종류가 맞지 않거나 지났으면 422.
   */
  http.put(url("/children/:cid/events/:eid/reminder"), async ({ params, request }) => {
    await networkDelay();
    const eid = String(params.eid);
    const { lead } = (await request.json()) as Partial<ReminderRequest>;
    if (!isReminderLead(lead)) {
      return apiError(400, "validation_failed", "요청 형식이 올바르지 않아요", {
        fields: ["lead"],
      });
    }
    // 🚨 목의 저장된 일정은 이것 하나다 (`calendarEventsOn`).
    if (eid !== confirmedEvent.id) return apiError(404, "not_found", "일정을 찾을 수 없어요");

    const refused = refuseReminder(lead, confirmedEvent.starts_at, confirmedEvent.all_day);
    if (refused) return refused;

    myReminders.set(eid, { lead });
    const body: ReminderResponse = { reminder: { lead } };
    return HttpResponse.json(body);
  }),

  /** 내 알림 지우기 — 서버와 같이 **없어도 204** 다. 두 번 눌러도 결과가 같다. */
  http.delete(url("/children/:cid/events/:eid/reminder"), async ({ params }) => {
    await networkDelay();
    myReminders.delete(String(params.eid));
    return new HttpResponse(null, { status: 204 });
  }),

  /**
   * 준비물 체크 — 서버 `PATCH /children/{cid}/event-items/{iid}` (#318 3/3 · PR #335) 와 같다.
   * 응답의 준비물은 일정 응답의 `items[]` 와 같은 모양이다.
   *
   * 🚨 **체크 시각은 상태가 바뀔 때만 새로 정한다.** 미체크 → 체크만 지금 시각, 체크 → 체크는
   *    처음 시각 그대로, 해제는 `null`.
   * 🚨 **`withIdempotency` 로 감싸지 않는다.** 최종 상태를 보내는 요청이라 몇 번 보내도 결과가 같다.
   *    순서가 뒤바뀌는 것은 서버도 못 막아서 화면이 준비물마다 요청을 한 줄로 보낸다 (`PreparedCheckbox`).
   */
  http.patch(url("/children/:cid/event-items/:iid"), async ({ params, request }) => {
    await networkDelay();
    const iid = String(params.iid);
    const { is_prepared: isPrepared } = (await request.json()) as { is_prepared: boolean };

    const item = confirmedEvent.items.find((each) => each.item_id === iid);
    const before = preparedItems.get(iid);
    if (!item || !before) return apiError(404, "not_found", "그 준비물을 찾지 못했어요");

    const after: PreparedState = {
      is_prepared: isPrepared,
      prepared_at: !isPrepared
        ? null
        : before.is_prepared
          ? before.prepared_at
          : new Date().toISOString(),
    };
    preparedItems.set(iid, after);

    const body: EventItemUpdateResponse = { item: { ...item, ...after } };
    return HttpResponse.json(body);
  }),
];
