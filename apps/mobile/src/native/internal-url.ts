/**
 * 이 주소를 웹뷰 안에 둬도 되는가 — 출처가 우리 웹이고, **API 경로가 아니어야** 한다.
 *
 * 출처만 보면 nginx 로 API 를 웹과 같은 주소(`/api`) 아래에 두는 날 두 길이 조용히 깨진다 (#273).
 * - 약관 "전문 보기"(`/api/v1/policies/…`)가 웹뷰 안에서 열려 가입 동의 화면을 덮는다.
 *   iOS 는 뒤로가기가 없어서 돌아올 방법이 없다 (#179).
 * - 로그인 시작(`/api/v1/auth/{provider}`)이 `isAuthStartUrl` 까지 가지 못하고 웹뷰 안에서 카카오로
 *   넘어간다. 복귀(`icatch://auth`)를 받을 곳이 없어 로그인이 끝나지 않는다.
 *
 * 🚨 **`/api` 는 API 의 모든 엔드포인트가 사는 경로다** (`apps/web/src/lib/env.ts` `API_BASE_URL`).
 *    웹에는 이 경로가 없다. nginx 가 다른 경로로 정해지면 여기를 같이 고친다.
 *
 * 🚨 **API 주소를 셸에 알리지 않는다.** 경로만 본다 — 알면 `EXPO_PUBLIC_*` 가 하나 늘고 배포마다
 *    맞춰야 한다 (`auth-session.ts` 머리말과 같은 이유).
 */

const API_PATH = /^\/api(\/|$)/;

export function isInternalUrlOf(url: string, allowedOrigin: string): boolean {
  let parsed: URL;
  try {
    parsed = new URL(url);
  } catch {
    return false;
  }
  if (parsed.origin !== allowedOrigin) return false;
  // `/apis` · `/api-docs` 처럼 `/api` 로 시작만 하는 웹 경로는 웹뷰 안에 남긴다.
  return !API_PATH.test(parsed.pathname);
}
