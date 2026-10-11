/**
 * 카카오 로그인 — **시작 URL 을 인앱 인증 세션으로 열고, 돌아온 주소를 웹의 복귀 화면에 싣는다.**
 *
 * 로그인은 웹이 한다. 웹이 서버가 준 시작 URL 로 떠나면(`window.location.href`) 셸은 그 이동 하나만
 * 가로채 Custom Tabs(iOS 는 `ASWebAuthenticationSession`)로 열고, 서버가 `icatch://auth?…` 로
 * 돌려보내면 그 쿼리를 그대로 `/auth/callback` 에 붙여 웹뷰를 옮긴다. 그 뒤는 웹·앱이 같은 코드다.
 *
 * 🚨 **셸은 URL 을 열고 URL 을 돌려받을 뿐이다.** 코드를 교환하지도, 토큰을 받지도 않는다
 *    (CLAUDE.md §1 "토큰을 들고 있지 않는다"). 카카오 네이티브 SDK 를 넣지 않는 이유가 이것이다.
 *
 * 🚨 **웹뷰에 태우지 않는다.** 카카오 계정 입력창이 우리 웹뷰에 뜨면 앱이 그 입력을 들여다볼 수 있는
 *    구조가 되고, 사용자는 주소창을 못 봐서 피싱과 구분하지 못한다. 그렇다고 시스템 브라우저로
 *    내보내면 앱 밖으로 두 번 전환되고 복귀를 받을 곳도 없다 (#210).
 *
 * 정본: docs/web/kakao-login-v1.md §5
 */

/** 서버가 앱으로 돌려보내는 주소 (`AUTH_RETURN_URL_APP`). `app.json` 의 `scheme` 과 짝이다. */
export const AUTH_RETURN_URL = "icatch://auth";

/** 웹의 복귀 화면. `apps/web/src/lib/auth/oauth.ts` 의 `CALLBACK_PATH` 와 같은 경로다. */
const CALLBACK_PATH = "/auth/callback";

/**
 * 로그인 시작 URL — `GET /api/v1/auth/{provider}`.
 *
 * 🚨 경로를 끝까지 맞춘다. `/status` 는 화면이 fetch 로 부르는 것이라 이동으로 오지 않지만,
 *    `/callback` 은 카카오가 돌려보내는 자리라 여기 걸리면 인증 세션이 그 안에서 또 열린다.
 */
const AUTH_START_PATH = /^\/api\/v1\/auth\/[a-z]+\/?$/;

/**
 * 이 이동이 로그인 시작인가.
 *
 * 오리진은 보지 않는다 — 셸은 API 주소를 모른다(알면 `EXPO_PUBLIC_*` 가 하나 늘고 배포마다 맞춰야
 * 한다). 경로가 같은 엉뚱한 주소가 걸려도 인앱 브라우저로 열릴 뿐 웹뷰에 들어오지 않으므로,
 * 다른 외부 링크와 위험이 같다.
 */
export function isAuthStartUrl(url: string): boolean {
  let parsed: URL;
  try {
    parsed = new URL(url);
  } catch {
    return false;
  }
  if (parsed.protocol !== "https:" && parsed.protocol !== "http:") return false;
  return AUTH_START_PATH.test(parsed.pathname);
}

/**
 * 돌아온 주소 → 웹뷰가 갈 복귀 화면 주소. 우리 주소가 아니면 `null`.
 *
 * ⚠️ 커스텀 스킴을 `URL` 로 파싱하지 않는다 — 엔진마다 `icatch://auth?…` 의 host · pathname 을
 *    다르게 읽는다. 앞자리를 문자열로 맞추고 쿼리는 손대지 않고 넘긴다(웹 · 서버가 같은 모양을 쓴다).
 *
 * 🚨 돌아갈 오리진은 **지금 웹뷰가 띄운 곳**(`WEB_URL`)이다. 고정 주소로 보내면 개발 빌드에서
 *    로컬 서버가 발급한 코드를 다른 사이트가 받게 되고, 거기엔 bind 가 없어 반드시 실패한다.
 */
export function callbackUrlFromReturn(returnUrl: string, webUrl: string): string | null {
  if (!returnUrl.startsWith(AUTH_RETURN_URL)) return null;
  const query = returnUrl.slice(AUTH_RETURN_URL.length);
  // `icatch://authXXX` 는 남이다. 서버가 실어 보내는 것은 쿼리뿐이다.
  if (query !== "" && !query.startsWith("?")) return null;
  return `${webUrl}${CALLBACK_PATH}${query}`;
}

/**
 * 웹뷰를 복귀 화면으로 옮기는 스크립트.
 *
 * 🚨 **`source` 를 바꾸지 않는다.** 같은 오류로 두 번 돌아오면(`?error=invalid_state` 등) 주소가
 *    문자열까지 같아서 React 가 바뀐 것으로 보지 않고, 웹뷰는 제자리에 남는다. 그래서 이동을 직접 건다.
 * 🚨 **웹뷰를 remount 하지 않는다.** 같은 웹뷰 · 같은 오리진 안의 이동이라 `sessionStorage` 에 둔
 *    bind 가 살아 있다 — 다시 만들면 bind 가 사라져 교환이 `invalid_handoff` 로 끝난다.
 * `replace` 인 이유 — 복귀 화면은 교환 뒤 스스로 다음 화면으로 넘어가는 경유지라 기록에 남길 게 없다.
 */
export function navigateScript(url: string): string {
  return `window.location.replace(${JSON.stringify(url)}); true;`;
}
