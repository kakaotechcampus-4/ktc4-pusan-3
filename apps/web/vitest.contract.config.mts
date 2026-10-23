import { defineConfig } from "vitest/config";

/**
 * 웹 API 타입 ↔ 서버 OpenAPI 비교 (#559). `make web-contract` 가 서버 계약을 덤프한 뒤 돈다.
 *
 * 기본 `pnpm test`(`vitest.config.ts`)와 나눈 이유 — 서버 계약을 뽑으려면 Python(`apps/api`)이 있어야 한다.
 * 웹만 만지는 사람의 `pnpm test` 가 그것 때문에 멈추면 안 된다.
 */
export default defineConfig({
  test: {
    environment: "node",
    include: ["contract/**/*.test.ts"],
  },
});
