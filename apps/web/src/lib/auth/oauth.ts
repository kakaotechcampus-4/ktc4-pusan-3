/**
 * 로그인 **시작**. 토큰을 받는 지점이 한 곳이라는 성질은 그대로고, 그 지점이 여기다.
 *
 * 🚨 로그인은 화면이 부르는 API 가 아니라 **페이지 이동**이다. 서버가 준 절대 URL 로
 *    떠났다가 `/auth/callback` 으로 돌아온다. 그래서 이 함수는 값을 돌려주지 않는다.
 *
 * 정본: docs/web/kakao-login-v1.md §4-2 · 서버 계약은 docs/api/auth-kakao-v1.md §3
 */

import type { AuthClient, AuthProvider } from "@/lib/api/types";
import { createBind } from "./oauth-bind";

export class OAuthUnavailableError extends Error {
  constructor() {
    super("로그인이 아직 연결되지 않았어요. 잠시 후 다시 시도해 주세요.");
    this.name = "OAuthUnavailableError";
  }
}

/**
 * 🚨 목 서버는 실제 OAuth 왕복을 흉내 낼 수 없다 — 외부 오리진 전체 페이지 이동이라
 *    서비스 워커가 못 잡는다. 개발 + 목 서버일 때만 카카오 왕복을 건너뛰고 콜백부터 태운다.
 *    프로덕션 빌드에서는 이 분기가 통째로 떨어져 나가서, 로그인이 되는 것처럼 보이는
 *    경로가 남지 않는다.
 */
const MOCK_ONLY =
  process.env.NODE_ENV !== "production" && process.env.NEXT_PUBLIC_API_MOCKING === "enabled";

const PLACEHOLDER_CODE = "dev-placeholder-code";

export const CALLBACK_PATH = "/auth/callback";

/** 앱 웹뷰인가. 셸이 `applicationNameForUserAgent` 로 UA 에 붙인 표식을 본다. */
export function isAppShell(): boolean {
  if (typeof navigator === "undefined") return false;
  return navigator.userAgent.includes("YukameoApp/");
}

export function authClient(): AuthClient {
  return isAppShell() ? "app" : "web";
}

/**
 * 시작 결과. `left` 는 이 페이지를 떠났다는 뜻이고, `internal` 은 목에서 카카오 왕복을
 * 건너뛴 경우라 호출자가 라우터로 이동한다 (앱 내부 이동을 `location` 으로 하지 않는다).
 */
export type LoginStart = { kind: "left" } | { kind: "internal"; path: string };

/**
 * `status.ready` 가 false 면 시작하지 않는다 — 카카오 동의 화면까지 걸어간 뒤 실패하거나,
 * 더 나쁘면 카카오 에러 페이지에 사용자가 버려진다.
 */
export function startOAuthLogin(
  provider: AuthProvider,
  status: { ready: boolean; start_url: string },
): LoginStart {
  if (!status.ready) throw new OAuthUnavailableError();

  // 복귀 경로는 여기서 만들지 않는다 — 아래 rememberReturnPath 주석 참고.
  rememberProvider(provider);
  const bind = createBind();

  if (MOCK_ONLY) return { kind: "internal", path: `${CALLBACK_PATH}?code=${PLACEHOLDER_CODE}` };

  // 🚨 서버가 준 절대 URL 로 이동한다. 상대경로로 가면 Next 오리진에서 출발하는데 카카오는
  //    API 오리진으로 돌려보내서, 시작 때 심은 state 쿠키가 서버 콜백에 실리지 않는다.
  //    로컬은 포트가 달라도 쿠키가 공유돼 우연히 통과한다 — 배포 기준으로 판단할 것.
  const url = new URL(status.start_url);
  // 🚨 URL 이 아니라 열거값을 보낸다. 복귀 대상은 서버 환경변수에서 온다 —
  //    프론트가 URL 을 넘기는 통로가 열리면 그게 오픈 리다이렉트다.
  url.searchParams.set("client", authClient());
  url.searchParams.set("bind", bind);
  window.location.href = url.toString();
  return { kind: "left" };
}

/* ── provider 기억 ──────────────────────────────────────────────────────
 * 복귀 경로에 provider 가 없다 (`/auth/callback?code=`). 어느 엔드포인트로 교환할지
 * 알아야 해서 시작 시점에 저장해 둔다. 서버가 복귀 URL 에 실어주면 이 저장은 없어진다.
 */

const PROVIDER_KEY = "yukameo.oauth.provider";
const DEFAULT_PROVIDER: AuthProvider = "kakao";

function rememberProvider(provider: AuthProvider): void {
  sessionStorage.setItem(PROVIDER_KEY, provider);
}

export function readProvider(): AuthProvider {
  // 저장이 유실돼도 로그인이 죽지 않게 kakao 로 떨어진다 (provider 가 하나뿐인 동안 안전하다).
  const stored = sessionStorage.getItem(PROVIDER_KEY);
  return stored === null ? DEFAULT_PROVIDER : (stored as AuthProvider);
}

export function clearProvider(): void {
  sessionStorage.removeItem(PROVIDER_KEY);
}

/* ── 가입 대기표 (consent_code) ─────────────────────────────────────────
 * 신규 회원은 교환 응답으로 토큰 대신 `consent_code` 를 받는다. 동의 화면이 그걸 들고
 * `/signup` 을 부르면 그때 계정이 만들어진다 (TTL 10분).
 *
 * 🚨 URL 이 아니라 sessionStorage 에 둔다 — 가입 대기표가 주소창·브라우저 기록에 남지 않게.
 */

const CONSENT_CODE_KEY = "yukameo.oauth.consent_code";

export function rememberConsentCode(code: string): void {
  sessionStorage.setItem(CONSENT_CODE_KEY, code);
}

export function readConsentCode(): string | null {
  return sessionStorage.getItem(CONSENT_CODE_KEY);
}

export function clearConsentCode(): void {
  sessionStorage.removeItem(CONSENT_CODE_KEY);
}

/* ── 복귀 경로 ──────────────────────────────────────────────────────────
 * "로그인 후 원래 보던 화면으로" 는 프론트 책임이다. 서버가 경로를 받으면 그것도
 * 오픈 리다이렉트 표면이 된다 (문서 §3-5).
 *
 * ⚠️ 저장 시점이 문서(§4-2)와 다르다. 로그인을 **시작할 때** 현재 경로를 저장하면 그 시점의
 *    경로는 항상 로그인 화면(`/`)이라 복원할 게 없다. 실제로 복원이 필요한 곳은 세션이
 *    끊겨 **튕겨 나오는 지점** — AuthGate 와 client.ts 의 401 처리 — 이므로 거기서 저장한다.
 */

const RETURN_KEY = "yukameo.oauth.return";

export function rememberReturnPath(path: string): void {
  if (!isInternalPath(path)) return;
  if (path === "/" || path.startsWith(`${CALLBACK_PATH}`)) return;
  sessionStorage.setItem(RETURN_KEY, path);
}

/** 한 번 쓰고 지운다. 유효하지 않으면 null — 호출자는 기본 경로로 간다. */
export function consumeReturnPath(): string | null {
  const stored = sessionStorage.getItem(RETURN_KEY);
  sessionStorage.removeItem(RETURN_KEY);
  return stored !== null && isInternalPath(stored) ? stored : null;
}

/**
 * 🚨 저장값을 그대로 이동에 쓰지 않는다. 서버가 오픈 리다이렉트를 막았는데 프론트가
 *    `location = 저장값` 으로 다시 열면 의미가 없다. `/` 로 시작하고 `//` 가 아닌 값만 통과.
 *    (`//evil.com` 은 프로토콜 상대 URL 이라 외부로 나간다.)
 */
function isInternalPath(path: string): boolean {
  return path.startsWith("/") && !path.startsWith("//");
}

/** 지금 보고 있는 경로 (쿼리 포함). 튕겨 나가기 전에 저장할 값이다. */
export function currentPath(): string {
  if (typeof window === "undefined") return "/";
  return `${window.location.pathname}${window.location.search}`;
}
