import { defineConfig, devices } from "@playwright/test";

import { BROWSER_TIME_ZONE } from "./e2e/time-zone";
import { API_DIR, API_PORT, WEB_ORIGIN, WEB_PORT, apiEnv } from "./e2e-live/server-env";

/**
 * 실서버 e2e (#756). 화면이 **실제 api 와 실제 DB** 위에서 이어지는지 본다.
 * 목 서버로 화면 규칙을 거는 `playwright.config.ts`(`e2e/`)와 따로 돈다 — 무엇을 어디서 거는지는
 * `docs/web/mock-and-tests-v1.md` §2 "실서버 e2e".
 *
 * 띄우는 것은 api(uvicorn)와 web(`next start`) 둘이다. DB 는 띄우지 않는다 — 로컬은 개발 DB 서버에
 * e2e 용 DB 를 만들어 두고(`make web-e2e-live`), CI 는 서비스 컨테이너다. 마이그레이션도 그 전에 끝나 있다.
 *
 * 🚨 **`next start` 다 — 빌드가 먼저 있어야 한다.** 목 코드가 빠진 프로덕션 빌드로 돌아야 실서버를
 *    탔다고 말할 수 있다. 빌드는 `NEXT_PUBLIC_API_BASE_URL=http://localhost:8100` 으로 한다 (`NEXT_PUBLIC_*`
 *    는 빌드에 박힌다). `make web-e2e-live` 와 CI 가 빌드부터 한다.
 * 🚨 이미 떠 있는 서버를 쓰지 않는다 (`reuseExistingServer: false`). 다른 설정으로 떠 있던 서버에 붙으면
 *    LLM 키가 살아 있는 api 를 탈 수 있다.
 */
export default defineConfig({
  testDir: "./e2e-live",
  outputDir: "./test-results/live",
  // 🚨 테스트마다 보호자를 새로 심어서 서로의 데이터를 보지 않는다. 그래도 api 는 워커 1개라
  //    (`--workers 1` · #134) 동시에 많이 붙이면 느려질 뿐이다.
  fullyParallel: true,
  workers: process.env.CI ? 2 : undefined,
  forbidOnly: !!process.env.CI,
  // 🚨 다시 돌리지 않는다. 실서버 쪽 흔들림을 재시도로 덮으면 이 스위트를 둔 이유가 없어진다.
  retries: 0,
  timeout: 60_000,
  expect: { timeout: 10_000 },
  reporter: process.env.CI
    ? [["github"], ["html", { open: "never", outputFolder: "playwright-report/live" }]]
    : [["list"]],
  use: {
    baseURL: WEB_ORIGIN,
    viewport: { width: 390, height: 844 },
    timezoneId: BROWSER_TIME_ZONE,
    locale: "ko-KR",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"], viewport: { width: 390, height: 844 } },
    },
  ],
  // 떠 있는지는 `127.0.0.1` 로 묻는다 — uvicorn 이 기본으로 IPv4 에만 붙는다. 화면 주소(`baseURL`)는
  // 쿠키 때문에 `localhost` 그대로 둔다 (`e2e-live/server-env.ts`).
  webServer: [
    {
      command: `uv run --no-sync uvicorn app.main:app --port ${API_PORT}`,
      cwd: API_DIR,
      url: `http://127.0.0.1:${API_PORT}/health`,
      env: apiEnv(),
      reuseExistingServer: false,
      timeout: 120_000,
      stdout: "pipe",
      stderr: "pipe",
    },
    {
      command: `pnpm exec next start --port ${WEB_PORT}`,
      url: `http://127.0.0.1:${WEB_PORT}`,
      reuseExistingServer: false,
      timeout: 60_000,
      // 🚨 `next start` 는 런타임에 `.env.local` 을 읽는다. 거기 `ALERT_WEBHOOK_URL` 이 있으면 서버 오류가
      //    팀 Discord 에 `[prod]` 로 찍힌다 (`src/instrumentation.ts`). 빈 값으로 덮는다 — @next/env 는
      //    이미 있는 환경변수를 덮지 않는다.
      env: { NEXT_TELEMETRY_DISABLED: "1", ALERT_WEBHOOK_URL: "", APP_ENV: "local" },
    },
  ],
});
