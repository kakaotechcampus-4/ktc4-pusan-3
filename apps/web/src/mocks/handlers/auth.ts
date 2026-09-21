import { http, HttpResponse } from "msw";

import { API_BASE_URL } from "@/lib/env";
import { me, meNeedingConsent, MOCK_TOKEN, PARENT_ID } from "../fixtures";
import { currentScenario } from "../scenario";
import { apiError, networkDelay, url } from "./helpers";
import type { WithdrawResponse } from "@/lib/api/types";
import { consentEffective, recordConsent } from "./settings";

/**
 * 로그인 · 동의. 정본은 docs/api/auth-kakao-v1.md §3, 프론트 쪽은 docs/web/kakao-login-v1.md.
 *
 * 🚨 실제 OAuth 왕복은 여기서 흉내 낼 수 없다 — 카카오로 나가는 전체 페이지 이동이라
 *    서비스 워커가 못 잡는다. 목이 덮는 것은 시작 전(`status`)과 돌아온 뒤(`교환`·`가입`)다.
 *    중간(카카오 왕복)은 `lib/auth/oauth.ts` 의 MOCK_ONLY 분기가 건너뛴다.
 */

const EXPIRES_IN = 43_200; // 12시간

/** 세션 응답. `/auth/{provider}` 와 `/signup` 이 같은 모양을 돌려준다. */
function session(isNew: boolean, consentRequired: string[] = []) {
  return HttpResponse.json({
    token: MOCK_TOKEN,
    expires_in: EXPIRES_IN,
    is_new: isNew,
    parent: { id: PARENT_ID, nickname: me.nickname },
    consent_required: consentRequired,
  });
}

export const authHandlers = [
  // 🚨 ready: false 면 프론트가 버튼을 비활성화한다 — 죽은 버튼을 만들지 않는다.
  http.get(url("/auth/:provider/status"), async ({ params }) => {
    await networkDelay();
    const ready = currentScenario() !== "auth_unready";
    return HttpResponse.json({
      ready,
      // 실서버는 등록된 카카오 콜백 URL 의 오리진에서 파생한다. 목은 자기 주소로 충분하다.
      start_url: `${API_BASE_URL}/auth/${String(params.provider)}`,
    });
  }),

  /**
   * 🚨 **`/auth/*` 의 이름이 정해진 경로는 `:provider` 보다 먼저 선다.** msw 는 배열 순서대로
   *    맞추는데 `:provider` 가 `logout` · `withdraw` 까지 삼킨다. 실제로 `POST /auth/logout`
   *    이 교환 핸들러에 걸려 있었고, 본문이 없어서 500 으로 떨어졌는데도 화면은 로컬 세션을
   *    비우고 나가 버려서 **아무도 눈치채지 못했다.** 새 `/auth/<이름>` 을 더할 때도 여기다.
   */
  http.post(url("/auth/logout"), async () => {
    await networkDelay();
    return new HttpResponse(null, { status: 204 });
  }),

  /**
   * ⚠️ **계약서에도 `docs/api/auth-kakao-v1.md` 에도 없다** (`lib/api/types.ts` 의
   *    `WithdrawRequest` 주석). 화면을 끝까지 돌려 보려고 목에만 세운 제안이다.
   *
   * 🚨 목이라고 **아무거나 200 으로 돌려주지 않는다.** 화면이 읽은 유예기간을 같이 받고 안
   *    보내면 막는다 — 실서버가 그 값을 남겨야 "부모가 무엇을 읽고 눌렀는가" 를 알 수 있다.
   */
  http.post(url("/auth/withdraw"), async ({ request }) => {
    await networkDelay();
    const body = (await request.json()) as { acknowledged_grace_days?: number };
    if (typeof body.acknowledged_grace_days !== "number") {
      return apiError(400, "validation_failed", "acknowledged_grace_days 가 필요해요");
    }
    const res: WithdrawResponse = {
      purge_after: new Date(
        Date.now() + body.acknowledged_grace_days * 24 * 60 * 60 * 1000,
      ).toISOString(),
    };
    return HttpResponse.json(res);
  }),

  // 교환 — 계약서의 {access_token} 대신 {code, bind} 를 받는다. 응답은 두 갈래(union)다.
  http.post(url("/auth/:provider"), async ({ request }) => {
    await networkDelay();
    const body = (await request.json()) as { code?: string; bind?: string };

    // 서버가 형식을 검증한다. "보냈다" 만 확인하면 bind=1 로도 통과해 방어가 무의미해진다.
    if (!body.code || !body.bind) {
      return apiError(400, "validation_failed", "code · bind 가 필요해요");
    }

    // 신규 회원 — 동의 전에는 parent 가 없어서 토큰이 없다.
    if (currentScenario() === "consent") {
      return HttpResponse.json({ status: "consent_required", consent_code: "cc_mock" });
    }
    return session(false);
  }),

  // 가입 — parent · auth_identity · consent 를 한 트랜잭션에서 만든다.
  http.post(url("/auth/:provider/signup"), async ({ request }) => {
    await networkDelay();
    const body = (await request.json()) as {
      consent_code?: string;
      bind?: string;
      consents?: Array<{ scope: string }>;
    };
    if (!body.consent_code || !body.bind) {
      return apiError(400, "validation_failed", "consent_code · bind 가 필요해요");
    }

    const scopes = (body.consents ?? []).map((c) => c.scope);
    const missing = ["service_terms", "privacy_account"].filter((s) => !scopes.includes(s));
    if (missing.length > 0) {
      return apiError(403, "consent_required", "먼저 동의가 필요해요", { scopes: missing });
    }
    return session(true);
  }),

  http.get(url("/me"), async () => {
    await networkDelay();
    return HttpResponse.json(currentScenario() === "consent" ? meNeedingConsent : me);
  }),

  // append-only. 철회도 withdrawn 행을 추가하는 것이지 지우는 게 아니다.
  //
  // 🚨 가입 직후에는 아이 스코프(child_basic · child_health)가 child_id 없이 온다 —
  //    child_basic 없이 POST /children 이 403 이라 아이를 만들기 **전에** 받아야 하기 때문이다
  //    (계약서 §04 "동의는 저장보다 먼저다"). 실서버가 이걸 받아 주는지는 #18 확인 대상이고,
  //    목은 받아 준다. 서버가 거절하기로 하면 여기와 화면을 같이 고친다.
  http.post(url("/consents"), async ({ request }) => {
    await networkDelay();
    const body = (await request.json()) as {
      scope: string;
      action: string;
      policy_version?: string;
    };
    if (!body.policy_version) {
      return apiError(400, "validation_failed", "policy_version 이 필요해요");
    }
    // 🚨 effective 를 여기서 손으로 만들지 않는다. 10 설정의 `GET /consents` 와 같은 표를
    //    써야 "설정에서 껐는데 다시 켜져 있다" 가 안 생긴다 (handlers/settings.ts).
    const action = body.action === "withdrawn" ? "withdrawn" : "granted";
    const entry = recordConsent(body.scope, action, body.policy_version);
    return HttpResponse.json(
      {
        consent: { id: `cs_${Date.now()}`, ...entry },
        effective: consentEffective(),
      },
      { status: 201 },
    );
  }),
];
