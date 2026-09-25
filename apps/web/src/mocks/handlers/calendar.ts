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

  /**
   * 🚨 **수정 초안의 제출. 승인 게이트 ㉠ 이다** — create 는 `POST`, update 는 `PATCH` 로
   *    갈린다 (9/21 회의). ⚠️ 경로는 확정 전이다 (`docs/event/event-draft-flow-v1.md` §6).
   *
   * 🚨 **Idempotency-Key 를 요구하지 않는다.** `items` 가 최종 목록이라 같은 본문을 두 번 보내도
   *    결과가 같다 — 새 행을 만드는 POST 만 키가 필요하다 (`lib/api/idempotency.ts` 의 주석).
   *    이 판단이 틀리면 `idempotentPath` 표에 줄을 더하고 여기도 `withIdempotency` 로 감싼다.
   *
   * 🚨 **`items` 에서 빠진 `item_id` 는 삭제다.** 보낸 것만 남는 것이 계약이고 (#122),
   *    보호자가 승인 화면에서 준비물을 지울 수 있는 유일한 표현 방법이다.
   */
  http.patch(url("/events/:eid"), async ({ params, request }) => {
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
        status: "confirmed" as const,
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
