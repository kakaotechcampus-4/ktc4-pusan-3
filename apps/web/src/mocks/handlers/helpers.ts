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

/** 실서버 왕복 느낌. 로딩·스켈레톤이 실제로 보이는지 확인하려면 0 이면 안 된다. */
export async function networkDelay(ms = 220): Promise<void> {
  await delay(ms);
}

/** 모든 경로는 /api/v1 하위다. 핸들러가 base 를 직접 조립하지 않게 여기서만 만든다. */
export function url(path: string): string {
  return `${API_BASE_URL}${path}`;
}
