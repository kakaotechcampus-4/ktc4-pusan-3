/**
 * 카카오 `access_token` 을 **어디서 받는가** 를 정하는 한 곳.
 *
 * 아직 안 정해졌다 — 앱은 네이티브 SDK 가 토큰을 직접 주고(#26), 웹은 인가 코드를 서버가
 * 교환하는 흐름이라(#18) 두 경로가 다르다. 화면이 그걸 알 필요는 없어서 여기서 막아 둔다.
 * 결정되면 이 파일만 고치고 화면은 그대로 둔다.
 *
 * 🚨 자리표시 토큰은 **개발 환경 + 목 서버가 켜져 있을 때만** 나간다.
 *    프로덕션 빌드에서 로그인이 되는 것처럼 보이는 경로를 만들지 않는다.
 */

export class KakaoLoginUnavailableError extends Error {
  constructor() {
    super("카카오 로그인은 아직 연결 전이에요. 앱에서 다시 시도해 주세요.");
    this.name = "KakaoLoginUnavailableError";
  }
}

const MOCK_ONLY =
  process.env.NODE_ENV !== "production" && process.env.NEXT_PUBLIC_API_MOCKING === "enabled";

/** 목 서버가 받아 주는 자리표시 값. 실제 카카오 토큰이 아니다. */
const PLACEHOLDER_ACCESS_TOKEN = "dev-placeholder-access-token";

export async function requestKakaoAccessToken(): Promise<string> {
  // TODO(#26) 웹뷰 셸 브릿지 — 셸이 loginWithKakaoTalk() 으로 받은 토큰을 넘겨준다.
  // TODO(#18) 웹 브라우저 — 인가 코드를 서버로 보내는 경로. 요청 본문 모양부터 다르다.
  if (MOCK_ONLY) return PLACEHOLDER_ACCESS_TOKEN;
  throw new KakaoLoginUnavailableError();
}
