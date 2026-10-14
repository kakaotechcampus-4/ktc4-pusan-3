import { HttpResponse, delay } from "msw";

import { API_BASE_URL } from "@/lib/env";
import type { ApiErrorCode } from "@/lib/api/errors";

/** 계약서 §01 에러 봉투. 프론트의 ApiError 가 이 모양을 기대한다. */
export function apiError(
  status: number,
  code: ApiErrorCode,
  message: string,
  detail?: Record<string, unknown>,
) {
  return HttpResponse.json({ error: { code, message, ...(detail ? { detail } : {}) } }, { status });
}

/** 필수 동의가 없을 때. 저장이 아예 안 된 상태이고, 프론트는 deeplink 로 동의 화면에 보낸다. */
export function consentRequired(scope: string) {
  return apiError(403, "consent_required", "먼저 동의가 필요해요", {
    scopes: [scope],
    deeplink: "settings/consents",
  });
}

/**
 * 실서버 왕복 느낌. 로딩·스켈레톤이 실제로 보이는지 확인하려면 0 이면 안 된다.
 *
 * 🚨 **단위 테스트(vitest)에서는 기다리지 않는다** (#387). 같은 핸들러를 node 에서 돌리는데 거기엔
 *    보여 줄 화면이 없고, 요청마다 기다려서 `pnpm test` 가 190초 걸렸다(지연을 빼면 82초).
 *    브라우저(개발 화면 · Playwright e2e)는 그대로 기다린다 — e2e 는 "등록하는 중이에요" 처럼
 *    기다리는 동안의 화면을 이 지연에 기대어 확인한다.
 * 🚨 판정은 `NODE_ENV` 가 아니라 `VITEST` 다 — vitest 는 `NODE_ENV` 가 비어 있을 때만 `test` 를
 *    넣어서, 셸에 `NODE_ENV=development` 가 잡혀 있으면 지연이 조용히 되살아난다. `VITEST` 는
 *    vitest 가 언제나 세우고, 브라우저 번들에는 없다.
 */
export async function networkDelay(ms = 220): Promise<void> {
  if (process.env.VITEST) return;
  await delay(ms);
}

/** 모든 경로는 /api/v1 하위다. 핸들러가 base 를 직접 조립하지 않게 여기서만 만든다. */
export function url(path: string): string {
  return `${API_BASE_URL}${path}`;
}
