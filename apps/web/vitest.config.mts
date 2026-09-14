import { fileURLToPath } from "node:url";

import { defineConfig } from "vitest/config";

/**
 * 목이 계약과 어긋나는 것을 잡는 회귀 테스트만 돌린다.
 *
 * 화면 테스트(RTL·jsdom)는 아직 없다 — 컴포넌트가 없기 때문이다.
 * 지금 지켜야 하는 건 "목의 **동작**이 계약과 같은가" 하나라서 node 환경이면 충분하다.
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
