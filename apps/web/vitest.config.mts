import { fileURLToPath } from "node:url";

import { defineConfig } from "vitest/config";

/**
 * 목 · lib · 스토어를 node 에서 직접 돌리는 테스트.
 *
 * 화면은 여기서 그리지 않는다 — `e2e/` 의 Playwright 가 실제 브라우저로 연다 (#242).
 * 11-1 그래프(Recharts)처럼 jsdom 에서 크기가 0 이라 안 그려지는 화면이 있어서다.
 */
export default defineConfig({
  resolve: {
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  test: {
    environment: "node",
    include: ["src/**/*.test.ts"],
    setupFiles: ["./src/test/setup.ts"],
    env: {
      // lib/env.ts 는 값이 없으면 import 시점에 던진다. 실서버로는 안 나간다 — MSW 가 가로챈다.
      NEXT_PUBLIC_API_BASE_URL: "http://localhost:8000",
      NEXT_PUBLIC_API_MOCKING: "enabled",
    },
  },
});
