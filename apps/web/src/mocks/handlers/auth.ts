import { http, HttpResponse } from "msw";

import { API_BASE_URL } from "@/lib/env";
import { me, MOCK_TOKEN, PARENT_ID } from "../fixtures";
import { currentScenario } from "../scenario";
import { apiError, networkDelay, url } from "./helpers";
import type { WithdrawResponse } from "@/lib/api/types";
import { currentMe } from "./membership";
import { invalidPolicyVersion } from "./policies";
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
function session(
  isNew: boolean,
  nickname: string = me.nickname ?? "",
  consentRequired: string[] = [],
) {
  return HttpResponse.json({
    token: MOCK_TOKEN,
    expires_in: EXPIRES_IN,
    is_new: isNew,
    parent: { id: PARENT_ID, nickname },
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
   * 🚨 목이라고 **아무거나 200 으로 돌려주지 않는다.** 화면의 읽음 표시를 같이 받고 없거나
   *    `false` 면 `400` 이다. 서버도 이 값을 **저장하지 않고 확인용으로만** 쓰고 같은 코드로
   *    거절한다 (#182 리뷰) — 유예 없이 지우는 요청이라(#167), 화면 버그로 잘못 나간 탈퇴를
   *    서버가 한 번 더 막는 자리다. 목이 먼저 통과시키면 그 방어가 개발 중에 안 보인다.
   */
  http.post(url("/auth/withdraw"), async ({ request }) => {
    await networkDelay();
    const body = (await request.json()) as { acknowledged_immediate_deletion?: boolean };
    if (body.acknowledged_immediate_deletion !== true) {
      return apiError(400, "validation_failed", "acknowledged_immediate_deletion 이 필요해요");
    }
    const res: WithdrawResponse = { deleted_at: new Date().toISOString() };
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
      nickname?: string;
      consents?: Array<{ scope: string; policy_version?: string }>;
    };
    if (!body.consent_code || !body.bind) {
      return apiError(400, "validation_failed", "consent_code · bind 가 필요해요");
    }

    const scopes = (body.consents ?? []).map((c) => c.scope);
    const missing = ["service_terms", "privacy_account"].filter((s) => !scopes.includes(s));
    if (missing.length > 0) {
      return apiError(403, "consent_required", "먼저 동의가 필요해요", { scopes: missing });
    }

    /**
     * 🚨 **등록된 버전이 아니면 계정을 만들지 않는다** (실서버와 같은 검사 · #91).
     *    재현할 수 없는 문구에 "동의했다" 고 적지 않는 것이 이 검사의 이유다.
     *    🚨 대기표는 **소비하지 않는다** — 낡은 화면은 공격이 아니라 사용자다. 다시 받아
     *       확인만 하면 되고, 로그인부터 다시 하게 만들지 않는다 (§3-5).
     */
    const invalid = invalidPolicyVersion(body.consents ?? []);
    if (invalid) {
      return apiError(400, "policy_version_invalid", "동의 화면을 다시 불러와 주세요", invalid);
    }
    // 목이라고 아무거나 200 으로 돌려주지 않는다 — 화면이 앞 화면에서 받아 온 이름을
    // 실제로 보내는지 건다 (서버도 이 필드를 받는다 · #90).
    if (!body.nickname) {
      return apiError(400, "validation_failed", "nickname 이 필요해요");
    }

    // 🚨 **가입에서 받은 동의도 같은 표에 쌓는다** (`handlers/settings.ts`).
    //    선택 동의(`location`)를 가입 화면에서 켤 수 있게 되면서, 여기서 안 쌓으면
    //    10 설정이 "켰는데 꺼져 있다" 로 보인다 — 화면이 아니라 목이 거짓말하는 경우다.
    for (const consent of body.consents ?? []) {
      recordConsent(consent.scope, "granted", consent.policy_version ?? "unknown");
    }
    return session(true, body.nickname);
  }),

  // 🚨 아이 목록을 여기서 만들지 않는다 — `currentMe()` 가 정본이다. 신규 가입은 아이
  //    0명에서 시작하고, 아이는 `POST /children` 이나 초대 수락으로만 생긴다
  //    (`handlers/membership.ts` — 그러지 않으면 00-1·초대 화면을 열어 볼 수 없다).
  http.get(url("/me"), async () => {
    await networkDelay();
    return HttpResponse.json(currentMe());
  }),

  // append-only. 철회도 withdrawn 행을 추가하는 것이지 지우는 게 아니다.
  //
  // 🚨 **가입 흐름은 이 경로를 쓰지 않는다.** 계정 2건은 `/signup` 바디로, 아이 2건은
  //    `POST /children` 바디로 간다 (#96) — 동의를 아이 단위로 기록하면 `child_id` 없이
  //    저장할 수 없고, 그 id 는 아이를 만들어야 생기기 때문이다. 여기로 오는 것은
  //    10 설정에서 켜고 끄는 경우뿐이고, 그때는 `child_id` 가 있다.
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
    // 🚨 가입·아이 등록과 **같은 검사**다. 설정에서 켤 때만 낡은 버전을 받아 주면,
    //    같은 동의가 어디서 켜졌는지에 따라 다른 글에 묶인다.
    const staleVersion = invalidPolicyVersion([
      { scope: body.scope, policy_version: body.policy_version },
    ]);
    if (staleVersion) {
      return apiError(
        400,
        "policy_version_invalid",
        "동의 화면을 다시 불러와 주세요",
        staleVersion,
      );
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
