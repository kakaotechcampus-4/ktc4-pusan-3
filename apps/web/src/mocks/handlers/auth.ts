import { http, HttpResponse } from "msw";

import { me, meNeedingConsent, MOCK_TOKEN, PARENT_ID } from "../fixtures";
import { currentScenario } from "../scenario";
import { networkDelay, url } from "./helpers";

/**
 * 로그인 · 동의.
 *
 * 실제 OAuth 는 여기서 흉내 내지 않는다 — provider access_token 을 어디서 받을지가
 * 아직 안 정해졌다 (웹뷰 안에서 provider 페이지로 이동하면 셸이 시스템 브라우저로 넘긴다).
 * 목은 아무 access_token 이나 받고 토큰을 내준다. 실제 경로는 #18 과 함께 정한다.
 */
export const authHandlers = [
  http.post(url("/auth/:provider"), async () => {
    await networkDelay();
    const needsConsent = currentScenario() === "consent";
    return HttpResponse.json({
      token: MOCK_TOKEN,
      is_new: false,
      parent: { id: PARENT_ID, nickname: me.nickname },
      consent_required: needsConsent ? ["service_terms", "privacy_account"] : [],
    });
  }),

  http.get(url("/me"), async () => {
    await networkDelay();
    return HttpResponse.json(currentScenario() === "consent" ? meNeedingConsent : me);
  }),

  // append-only. 철회도 withdrawn 행을 추가하는 것이지 지우는 게 아니다.
  http.post(url("/consents"), async ({ request }) => {
    await networkDelay();
    const body = (await request.json()) as { scope: string; action: string };
    return HttpResponse.json(
      {
        consent: {
          id: `cs_${Date.now()}`,
          scope: body.scope,
          action: body.action,
          acted_at: new Date().toISOString(),
        },
        effective: {
          service_terms: true,
          privacy_account: true,
          child_basic: true,
          child_health: body.scope === "child_health" ? body.action === "granted" : true,
          quality_improve: false,
        },
      },
      { status: 201 },
    );
  }),
];
