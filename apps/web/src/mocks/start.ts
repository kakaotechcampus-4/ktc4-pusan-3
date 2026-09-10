import { API_BASE_URL } from "@/lib/env";

import { currentScenario, SCENARIOS, syncScenarioFromUrl } from "./scenario";

/**
 * 목을 켠다. 개발 환경 + NEXT_PUBLIC_API_MOCKING=enabled 일 때만이다.
 *
 * `process.env.NODE_ENV` 는 빌드 시점에 문자열로 치환되므로, 프로덕션 빌드에서는
 * 아래 조건이 항상 거짓이 되고 동적 import 가 통째로 떨어져 나간다 —
 * msw 는 프로덕션 번들에 들어가지 않는다.
 */
/**
 * 🚨 한 번만 시작한다. StrictMode 가 개발 환경에서 effect 를 두 번 돌리는데, 두 번째
 *    `worker.start()` 는 "cannot configure an already enabled network" 로 **거부된다.**
 *    그러면 호출부의 `.finally()` 가 즉시 실행돼 **워커가 뜨기 전에 화면이 그려지고**,
 *    첫 화면의 첫 요청이 목을 통과해 실서버로 나간다 (00 로그인의 status prefetch 가
 *    여기 걸렸다). 약속을 캐시해서 두 번째 호출이 같은 완료를 기다리게 한다.
 */
let starting: Promise<void> | null = null;

export function startMocks(): Promise<void> {
  starting ??= run();
  return starting;
}

async function run(): Promise<void> {
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
