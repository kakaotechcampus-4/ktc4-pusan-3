import { afterAll, afterEach, beforeAll } from "vitest";

import { resetRegisteredSafety } from "@/mocks/handlers/children";
import { resetIdempotencyStore } from "@/mocks/handlers/idempotency";
import { resetConfirmedEvents } from "@/mocks/handlers/suggestions";
import { server } from "@/mocks/server";

/**
 * 목은 프로세스 수명만큼 사는 상태를 들고 있다 (확정된 event · 저장된 응답).
 * 테스트끼리 그 상태가 새면 "두 번째 요청이 409" 같은 결과가 순서에 따라 바뀐다.
 */
beforeAll(() => {
  // 계약서에 없는 경로를 조용히 통과시키지 않는다.
  server.listen({ onUnhandledRequest: "error" });
});

afterEach(() => {
  server.resetHandlers();
  resetIdempotencyStore();
  resetConfirmedEvents();
  resetRegisteredSafety();
});

afterAll(() => {
  server.close();
});

/** scenario.ts 가 localStorage 를 읽는다. node 에는 없으니 최소한으로 채운다. */
const store = new Map<string, string>();
Object.defineProperty(globalThis, "localStorage", {
  configurable: true,
  value: {
    getItem: (key: string) => store.get(key) ?? null,
    setItem: (key: string, value: string) => void store.set(key, String(value)),
    removeItem: (key: string) => void store.delete(key),
    clear: () => store.clear(),
    key: (index: number) => [...store.keys()][index] ?? null,
    get length() {
      return store.size;
    },
  },
});
