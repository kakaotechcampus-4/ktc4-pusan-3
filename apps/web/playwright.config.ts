import { defineConfig, devices } from "@playwright/test";

/**
 * 화면 규칙 회귀 테스트 (#242). 최상위 CLAUDE.md §2 가 화면에서 지켜지는지를 **목 서버로** 건다.
 *
 * 🚨 실서버 E2E 가 아니다. 목은 개발 서버에서만 뜨므로(`mocks/start.ts` 가 NODE_ENV 로 막는다)
 *    `next build` 결과로는 돌릴 수 없다 — 그래서 `next dev` 를 띄운다.
 *
 * 🚨 Next 16 은 같은 폴더에서 `next dev` 를 두 개 띄우지 못한다 (lockfile). 이미 개발 서버가
 *    떠 있으면 그것을 그대로 쓴다. 그 서버가 목을 끈 채로 떠 있으면 첫 테스트가 "목을 통과한
 *    요청" 으로 실패한다 — `.env.local` 의 `NEXT_PUBLIC_API_MOCKING=enabled` 를 확인할 것.
 */
const PORT = Number(process.env.E2E_PORT ?? 3000);
const BASE_URL = `http://localhost:${PORT}`;

export default defineConfig({
  testDir: "./e2e",
  // 목 상태는 탭(페이지 JS) 안에만 산다 — 테스트끼리 공유하는 것이 없어서 병렬로 돌려도 된다.
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  // 개발 서버는 처음 여는 화면을 그때 컴파일한다. 첫 진입이 느린 것은 실패가 아니다.
  timeout: 60_000,
  expect: { timeout: 10_000 },
  retries: process.env.CI ? 1 : 0,
  // 컴파일이 몰리면 개발 서버가 느려진다. CI 러너(2코어)에서는 둘로 묶는다.
  workers: process.env.CI ? 2 : undefined,
  reporter: process.env.CI ? [["github"], ["html", { open: "never" }]] : [["list"]],
  use: {
    baseURL: BASE_URL,
    // 대부분 apps/mobile 웹뷰 안에서 보인다 (/web-smoke 와 같은 폭).
    viewport: { width: 390, height: 844 },
    // 날짜 표시는 한국 시간 기준이다 (lib/format.ts). 러너 시간대에 따라 문구가 바뀌지 않게 고정한다.
    timezoneId: "Asia/Seoul",
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
  webServer: {
    command: `pnpm exec next dev --port ${PORT}`,
    url: BASE_URL,
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
    // 🚨 `.env.local` 보다 여기 값이 이긴다 (Next 는 이미 있는 환경변수를 덮지 않는다).
    //    주소는 더미다 — 요청은 전부 목이 받는다.
    env: {
      NEXT_PUBLIC_API_BASE_URL: "http://localhost:8000",
      NEXT_PUBLIC_API_MOCKING: "enabled",
      NEXT_TELEMETRY_DISABLED: "1",
    },
  },
});
