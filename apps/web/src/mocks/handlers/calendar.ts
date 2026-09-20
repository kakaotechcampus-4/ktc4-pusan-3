import { http, HttpResponse } from "msw";

import type {
  CalendarDayResponse,
  CalendarDayUpdate,
  CalendarMonthResponse,
  EventItemUpdateResponse,
} from "@/lib/api/types";

import {
  calendarEventsOn,
  calendarMonth,
  calendarObservationsOn,
  confirmedEvent,
  readDiary,
  writeDiary,
} from "../fixtures";
import { currentScenario } from "../scenario";
import { apiError, networkDelay, url } from "./helpers";

/**
 * 09 캘린더 핸들러.
 *
 * 🚨 **일기는 관찰과 별도 저장소다** (계약서 §09). 목에서도 일기를 쓴다고 관찰이 늘지 않는다 —
 *    "일기는 관찰로 자동 추출하지 않는다" 를 화면이 지키는지 확인하려면 목이 먼저 지켜야 한다.
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

export const calendarHandlers = [
  http.get(url("/children/:cid/calendar"), async ({ request }) => {
    await networkDelay();
    const month = new URL(request.url).searchParams.get("month");
    if (!month) return apiError(422, "validation_failed", "month 가 필요해요");

    const body: CalendarMonthResponse = {
      days: currentScenario() === "empty" ? [] : calendarMonth(month),
    };
    return HttpResponse.json(body);
  }),

  http.get(url("/children/:cid/calendar/:date"), async ({ params }) => {
    await networkDelay();
    const date = String(params.date);

    if (currentScenario() === "empty") {
      const empty: CalendarDayResponse = { diary: null, events: [], observations: [] };
      return HttpResponse.json(empty);
    }

    const body: CalendarDayResponse = {
      diary: readDiary(date),
      events: withPrepared(date),
      observations: calendarObservationsOn(date),
    };
    return HttpResponse.json(body);
  }),

  http.put(url("/children/:cid/calendar/:date"), async ({ params, request }) => {
    await networkDelay();
    const date = String(params.date);
    const update = (await request.json()) as CalendarDayUpdate;

    // 🚨 PUT 이라 통째로 덮는다. 화면이 사진과 일정을 함께 실어 보내지 않으면 여기서 지워진다 —
    //    목이 그걸 봐주면 실서버에서만 사진이 사라진다.
    writeDiary(date, { text: update.text, image_urls: update.image_urls ?? [] });

    const body: CalendarDayResponse = {
      diary: readDiary(date),
      events: withPrepared(date),
      observations: calendarObservationsOn(date),
    };
    return HttpResponse.json(body);
  }),

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
