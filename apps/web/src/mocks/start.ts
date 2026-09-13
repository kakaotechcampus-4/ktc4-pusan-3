import { API_BASE_URL } from "@/lib/env";

import { currentScenario, SCENARIOS, syncScenarioFromUrl } from "./scenario";

/**
 * 목을 켠다. 개발 환경 + NEXT_PUBLIC_API_MOCKING=enabled 일 때만이다.
 *
 * `process.env.NODE_ENV` 는 빌드 시점에 문자열로 치환되므로, 프로덕션 빌드에서는
 * 아래 조건이 항상 거짓이 되고 동적 import 가 통째로 떨어져 나간다 —
 * msw 는 프로덕션 번들에 들어가지 않는다.
 */
export async function startMocks(): Promise<void> {
  if (process.env.NODE_ENV !== "development") return;
  if (process.env.NEXT_PUBLIC_API_MOCKING !== "enabled") return;

  syncScenarioFromUrl();

  const { worker } = await import("./browser");

  await worker.start({
    serviceWorker: { url: "/mockServiceWorker.js" },
    // Next 의 정적 자원까지 경고하면 콘솔이 묻힌다. 계약서 경로만 본다.
    onUnhandledRequest(request, print) {
      if (request.url.startsWith(API_BASE_URL)) print.warning();
    },
  });

  const scenario = currentScenario();
  console.info(
    `[mocks] 목 서버가 떴다 — 시나리오 "${scenario}" (${SCENARIOS[scenario]})\n` +
      `        바꾸려면 주소에 ?scenario=<${Object.keys(SCENARIOS).join(" | ")}>`,
  );
}
