import { http, HttpResponse } from "msw";

import type {
  CalendarDay,
  CalendarDayResponse,
  CalendarDayUpdate,
  CalendarMonthResponse,
  EventItemUpdateResponse,
  Observation,
} from "@/lib/api/types";

import {
  calendarEventDates,
  calendarEventsOn,
  confirmedEvent,
  myDiaryDates,
  readMyDiary,
  sharedPhotoDates,
  sharedPhotosOn,
  writeMyDiary,
} from "../fixtures";
import { currentScenario } from "../scenario";
import { apiError, networkDelay, url } from "./helpers";
import { withIdempotency } from "./idempotency";
import { listedObservations } from "./memories";

/**
 * 09 캘린더 핸들러. 서버(#318 1/3 · PR #325)와 같은 모양을 내린다.
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

/** 준비물 체크 상태. 🚨 프로세스 수명만큼 산다 — 테스트는 `resetCalendarState()` 로 되돌린다. */
const preparedItems = new Map<string, boolean>(
  confirmedEvent.items.map((item) => [item.item_id, item.is_prepared]),
);

export function resetCalendarState(): void {
  preparedItems.clear();
  for (const item of confirmedEvent.items) preparedItems.set(item.item_id, item.is_prepared);
}

function withPrepared(date: string) {
  return calendarEventsOn(date).map((event) => ({
    ...event,
    items: event.items.map((item) => ({
      ...item,
      is_prepared: preparedItems.get(item.item_id) ?? item.is_prepared,
    })),
  }));
}

/** 서버처럼 기간(`observed_from` ~ `observed_to`)이 그날과 겹치는 기록. 여러 날 기록은 걸친 날마다 나온다. */
function observationsOn(date: string): Observation[] {
  return listedObservations().filter((o) => o.observed_from <= date && date <= o.observed_to);
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

function calendarDay(date: string): CalendarDayResponse {
  return {
    my_diary: readMyDiary(date),
    shared_photos: sharedPhotosOn(date),
    events: withPrepared(date),
    observations: observationsOn(date),
  };
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
      };
      return HttpResponse.json(empty);
    }

    return HttpResponse.json(calendarDay(date));
  }),

  /**
   * ⚠️ **옮겨 갈 경로다** — 서버는 `PUT /children/{cid}/diary/{date}` · `{ content }` 로 받는다
   *    (#318 2/3). 응답이 그 PR 에서 정해져서 그때 옮긴다.
   *
   * 🚨 사진은 일기 밖에 있어서 여기서 건드리지 않는다. 일기를 덮어도 사진은 그대로다.
   */
  http.put(url("/children/:cid/calendar/:date"), async ({ params, request }) => {
    await networkDelay();
    const date = String(params.date);
    const update = (await request.json()) as CalendarDayUpdate;

    writeMyDiary(date, update.text);
    return HttpResponse.json(calendarDay(date));
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

      return HttpResponse.json({
        event: {
          ...confirmedEvent,
          id: eid,
          title: body.event.title,
          starts_at: body.event.starts_at,
          all_day: body.event.all_day,
          items: body.items.map((item, index) => ({
            item_id: item.item_id ?? `i_new_${index}`,
            item_name: item.item_name,
            // 🚨 초안은 체크 상태를 정하지 않는다 — 기존 행은 서버가 들고 있던 값을 지킨다 (#122 §4-3).
            is_prepared: item.item_id ? (preparedItems.get(item.item_id) ?? false) : false,
            prepared_at: null,
          })),
        },
      });
    }),
  ),

  http.patch(url("/event-items/:iid"), async ({ params, request }) => {
    await networkDelay();
    const iid = String(params.iid);
    const { is_prepared: isPrepared } = (await request.json()) as { is_prepared: boolean };

    const item = confirmedEvent.items.find((each) => each.item_id === iid);
    if (!item) return apiError(404, "not_found", "그 준비물을 찾지 못했어요");

    preparedItems.set(iid, isPrepared);

    const body: EventItemUpdateResponse = {
      item: {
        ...item,
        is_prepared: isPrepared,
        prepared_at: isPrepared ? new Date().toISOString() : null,
      },
    };
    return HttpResponse.json(body);
  }),
];
