import { http, HttpResponse } from "msw";

import { API_BASE_URL } from "@/lib/env";
import { me, MOCK_TOKEN, PARENT_ID } from "../fixtures";
import { currentScenario } from "../scenario";
import { apiError, networkDelay, url } from "./helpers";
import type { WithdrawResponse } from "@/lib/api/types";
import { currentMe } from "./membership";
import { invalidPolicyVersion } from "./policies";
import { recordConsent } from "./settings";

/**
 * 로그인 · 동의. 정본은 docs/api/auth-kakao-v1.md §3, 프론트 쪽은 docs/web/kakao-login-v1.md.
 *
 * 🚨 실제 OAuth 왕복은 여기서 흉내 낼 수 없다 — 카카오로 나가는 전체 페이지 이동이라
 *    서비스 워커가 못 잡는다. 목이 덮는 것은 시작 전(`status`)과 돌아온 뒤(`교환`·`가입`)다.
 *    중간(카카오 왕복)은 `lib/auth/oauth.ts` 의 MOCK_ONLY 분기가 건너뛴다.
 */

/** access 토큰 수명. 서버와 같은 15분이다. */
const EXPIRES_IN = 900;

/**
 * refresh 쿠키 대신 두는 표식. 교환 · 가입이 심고, 로그아웃 · 탈퇴가 지운다.
 *
 * 🚨 **화면 코드는 이 값을 읽지 않는다.** 실서버의 쿠키는 HttpOnly 라 화면이 못 읽는다. 목은
 *    서비스 워커라 쿠키를 다룰 수 없어서 표식을 저장소에 둘 뿐이고, 읽는 것은 아래 핸들러뿐이다.
 * `localStorage` 인 이유 — 새로고침하고 탭을 다시 열어도 남아야 "앱을 다시 켜도 로그인 유지" 를
 * 목에서 볼 수 있다. e2e 는 로그인한 채로 시작할 때 이 값을 심는다 (`e2e/fixtures.ts`).
 */
export const MOCK_REFRESH_KEY = "icatch.mock.refresh";

function hasRefreshCookie(): boolean {
  return typeof localStorage !== "undefined" && localStorage.getItem(MOCK_REFRESH_KEY) !== null;
}

function setRefreshCookie(present: boolean): void {
  if (typeof localStorage === "undefined") return;
  if (present) localStorage.setItem(MOCK_REFRESH_KEY, "1");
  else localStorage.removeItem(MOCK_REFRESH_KEY);
}

/**
 * `session_expired` 에서 처음 발급하는 access 토큰. 🚨 이 토큰으로 온 요청은 401 이다 —
 * 화면이 갱신한 뒤 다시 보내는지 본다 (`sessionGuardHandlers`).
 */
const EXPIRED_TOKEN = "mock-token-expired";
let issuedExpired = false;
/** `logout_unreached` 에서 로그아웃을 한 번 끊어 봤는가. 🚨 한 번만 끊는다 — 다시 누르면 닿아야 한다. */
let logoutCutOnce = false;

/**
 * 탈퇴가 끝났는가. 🚨 끝나면 `/me` 와 다시 보낸 탈퇴가 `401 unauthenticated` 다 — 서버는 커밋하면서
 * 그 보호자의 세션을 전부 지운다 (PR #384). 목이 계속 200 을 주면 "응답만 잃은 탈퇴" 를 화면이
 * 어떻게 끝내는지 볼 수 없다 (#389). 다음 로그인(교환 · 가입)에서 풀린다 — 새 계정이다.
 */
let withdrawn = false;
/** `withdraw_unreached` 에서 이미 한 번 끊어 봤는가. 🚨 한 번만 끊는다 — 다시 누르면 닿아야 한다. */
let unreachedOnce = false;
/** `withdraw_unconfirmed` 에서 탈퇴 뒤의 첫 `/me` 를 끊어 봤는가. 🚨 한 번만 끊는다. */
let probeCutOnce = false;

/** 목의 로그인 상태를 처음으로 되돌린다. 🚨 refresh 표식은 저장소에 있어서 테스트끼리 샌다. */
export function resetAuthState(): void {
  withdrawn = false;
  unreachedOnce = false;
  probeCutOnce = false;
  issuedExpired = false;
  logoutCutOnce = false;
  setRefreshCookie(false);
}

/** 탈퇴한 뒤에 들고 있던 토큰으로 온 요청. 서버에는 그 세션이 없다. */
function sessionGone() {
  return apiError(401, "unauthenticated", "다시 로그인해 주세요");
}

/** 세션 응답. `/auth/{provider}` 와 `/signup` 이 같은 모양을 돌려준다. */
function session(
  isNew: boolean,
  nickname: string = me.nickname ?? "",
  consentRequired: string[] = [],
) {
  // 탈퇴한 뒤 다시 로그인하면 새 계정이다 — 서버도 새로 가입시킨다 (PR #384).
  withdrawn = false;
  // 서버는 이 응답에 refresh 쿠키를 심는다.
  setRefreshCookie(true);
  return HttpResponse.json({
    token: MOCK_TOKEN,
    expires_in: EXPIRES_IN,
    is_new: isNew,
    parent: { id: PARENT_ID, nickname },
    consent_required: consentRequired,
  });
}

/**
 * 만료된 access 로 온 요청을 막는다 (`session_expired`). 🚨 **모든 핸들러보다 앞에 선다**
 * (`handlers/index.ts`). `/auth/*` 는 건드리지 않는다 — 갱신 자체가 그 아래다.
 *
 * 🚨 **경로 패턴(`/*`)이 아니라 조건으로 맞춘다.** 모든 경로에 맞는 핸들러가 응답 없이 넘기면
 *    msw 는 그 요청을 "처리됨" 으로 보고 실서버로 흘려보낸다 — 계약에 없는 경로를 잡는
 *    `onUnhandledRequest: "error"` 가 꺼진다. 조건에 안 맞는 요청은 이 핸들러에 아예 안 맞는다.
 */
export const sessionGuardHandlers = [
  http.all(
    ({ request }) =>
      currentScenario() === "session_expired" &&
      request.url.startsWith(API_BASE_URL) &&
      !new URL(request.url).pathname.includes("/auth/") &&
      request.headers.get("Authorization") === `Bearer ${EXPIRED_TOKEN}`,
    () => sessionGone(),
  ),
];

export const authHandlers = [
  /**
   * 갱신 — Bearer 가 아니라 refresh 쿠키로 인증한다 (`lib/api/types.ts` 의 `AuthRefreshResponse`).
   * 🚨 `:provider` 보다 먼저 선다 (아래 주석). 안 그러면 교환 핸들러가 `refresh` 를 provider 로 삼킨다.
   */
  http.post(url("/auth/refresh"), async () => {
    await networkDelay();
    // 🚨 서버에 닿지 못한 것이다 — 로그아웃이 아니다. 화면은 연결 확인을 세우고 로그인 화면으로 안 간다.
    if (currentScenario() === "session_unreachable") return HttpResponse.error();
    if (withdrawn || !hasRefreshCookie()) return sessionGone();
    const expired = currentScenario() === "session_expired" && !issuedExpired;
    if (expired) issuedExpired = true;
    return HttpResponse.json({
      token: expired ? EXPIRED_TOKEN : MOCK_TOKEN,
      expires_in: EXPIRES_IN,
    });
  }),

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
    // 🚨 서버에 닿지 못했다 — 세션도 쿠키도 그대로다. 화면이 나간 척하면 다시 켤 때 로그인돼 있다.
    if (currentScenario() === "logout_unreached" && !logoutCutOnce) {
      logoutCutOnce = true;
      return HttpResponse.error();
    }
    // 서버는 세션 행을 지우고 refresh 쿠키도 지운다.
    setRefreshCookie(false);
    return new HttpResponse(null, { status: 204 });
  }),

  /**
   * 서버 PR #384 와 같은 모양이다 (`lib/api/types.ts` 의 `WithdrawRequest` 주석).
   *
   * 🚨 목이라고 **아무거나 200 으로 돌려주지 않는다.** 화면의 읽음 표시를 같이 받고 없거나
   *    `false` 면 `400` 이다. 서버도 이 값을 **저장하지 않고 확인용으로만** 쓰고 같은 코드로
   *    거절한다 (#182 리뷰) — 유예 없이 지우는 요청이라(#167), 화면 버그로 잘못 나간 탈퇴를
   *    서버가 한 번 더 막는 자리다. 목이 먼저 통과시키면 그 방어가 개발 중에 안 보인다.
   */
  http.post(url("/auth/withdraw"), async ({ request }) => {
    await networkDelay();
    // 서버 순서와 같다 — 인증이 먼저다. 이미 끝난 탈퇴를 다시 보내면 본문을 보기 전에 401 이다.
    if (withdrawn) return sessionGone();
    const body = (await request.json()) as { acknowledged_immediate_deletion?: unknown };
    if (body.acknowledged_immediate_deletion !== true) {
      return apiError(400, "validation_failed", "acknowledged_immediate_deletion 이 필요해요");
    }

    const scenario = currentScenario();
    // 🚨 서버에 닿기 전에 끊긴다 — 아무것도 안 지웠다. 확인(`/me`)하면 200 이어야 한다.
    if (scenario === "withdraw_unreached" && !unreachedOnce) {
      unreachedOnce = true;
      return HttpResponse.error();
    }
    withdrawn = true;
    setRefreshCookie(false);
    // 🚨 커밋한 **뒤에** 응답이 사라진다 (PR #384 "응답을 못 받았을 때"). 다시 누르면 401 이다.
    if (scenario === "withdraw_lost" || scenario === "withdraw_unconfirmed") {
      return HttpResponse.error();
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
      recordConsent(null, consent.scope, "granted", consent.policy_version ?? "unknown");
    }
    return session(true, body.nickname);
  }),

  // 🚨 아이 목록을 여기서 만들지 않는다 — `currentMe()` 가 정본이다. 신규 가입은 아이
  //    0명에서 시작하고, 아이는 `POST /children` 이나 초대 수락으로만 생긴다
  //    (`handlers/membership.ts` — 그러지 않으면 00-1·초대 화면을 열어 볼 수 없다).
  http.get(url("/me"), async () => {
    await networkDelay();
    if (withdrawn) {
      // 🚨 확인까지 끊긴다 — 화면은 모르는 채로 "다시 눌러 주세요" 를 세우고, 다시 누른 탈퇴가 401 이다.
      if (currentScenario() === "withdraw_unconfirmed" && !probeCutOnce) {
        probeCutOnce = true;
        return HttpResponse.error();
      }
      return sessionGone();
    }
    return HttpResponse.json(currentMe());
  }),
];
