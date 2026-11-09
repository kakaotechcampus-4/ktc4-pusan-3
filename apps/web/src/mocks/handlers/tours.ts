import { http, HttpResponse } from "msw";

import type { SeenTours } from "@/lib/api/types";
import { TOUR_KEYS, type TourKey } from "@/lib/tour/types";
import { apiError, networkDelay, url } from "./helpers";

/**
 * 화면 투어를 본 기록 (#696 · 서버 `routers/tours.py` 와 같은 경로 · `docs/web/screen-tour-v1.md` §2).
 *
 * 🚨 **브라우저에서는 `localStorage` 에 든다.** 실서버에서 "봤음" 은 새로고침해도 남는다 — 목 메모리에
 *    두면 `page.goto` · `reload` 마다 지워져서 "다시 들어온 화면에서는 안 뜬다" 를 목에서 볼 수 없다.
 *    e2e 바탕은 이 키로 "다 봤음" 을 심는다 (`e2e/fixtures.ts` 의 `toursSeen`). 목 보호자는 한 명이라
 *    키에 보호자 id 를 넣지 않는다.
 * 🚨 **화면 코드는 이 값을 읽지 않는다.** 화면은 `GET /me/tours` 로만 안다 (`lib/tour/seen.ts`).
 * 단위 테스트(node)에는 저장소가 없어서 메모리에 든다 (`resetTourState()` 를 `afterEach` 에 건다).
 */
export const MOCK_TOURS_KEY = "icatch.mock.tours";

let memory: TourKey[] = [];

function hasStorage(): boolean {
  return typeof window !== "undefined" && typeof window.localStorage !== "undefined";
}

function readTours(): TourKey[] {
  if (!hasStorage()) return memory;
  try {
    const parsed: unknown = JSON.parse(localStorage.getItem(MOCK_TOURS_KEY) ?? "[]");
    return Array.isArray(parsed) ? parsed.filter(isTourKey) : [];
  } catch {
    return [];
  }
}

function writeTours(keys: TourKey[]): void {
  if (!hasStorage()) {
    memory = keys;
    return;
  }
  if (keys.length === 0) localStorage.removeItem(MOCK_TOURS_KEY);
  else localStorage.setItem(MOCK_TOURS_KEY, JSON.stringify(keys));
}

function isTourKey(value: unknown): value is TourKey {
  return typeof value === "string" && (TOUR_KEYS as readonly string[]).includes(value);
}

export function resetTourState(): void {
  memory = [];
}

export const tourHandlers = [
  http.get(url("/me/tours"), async () => {
    await networkDelay();
    return HttpResponse.json<SeenTours>({ seen: readTours() });
  }),

  // 이미 본 것이면 아무것도 바꾸지 않는다 — 서버는 처음 본 시각을 그대로 둔다 (ON CONFLICT DO NOTHING).
  http.put(url("/me/tours/:key"), async ({ params }) => {
    await networkDelay();
    const key = params.key;
    if (!isTourKey(key)) return apiError(422, "validation_failed", "알 수 없는 화면이에요");
    const seen = readTours();
    if (!seen.includes(key)) writeTours([...seen, key]);
    return new HttpResponse(null, { status: 204 });
  }),

  http.delete(url("/me/tours"), async () => {
    await networkDelay();
    writeTours([]);
    return new HttpResponse(null, { status: 204 });
  }),
];
