import path from "node:path";

/**
 * 실서버 e2e 가 띄우는 두 서버의 주소와 api 환경변수 (#756).
 *
 * 설정(`playwright.live.config.ts`)과 시드(`fixtures.ts`)가 같은 값을 써야 한다. 둘이 다른 DB 를
 * 보면 시드한 보호자를 api 가 모르고, 화면은 "로그인이 풀렸다" 로 엉뚱한 자리에서 실패한다.
 *
 * 🚨 포트는 개발 서버(3000 · 8000)와 겹치지 않게 둔다. 로컬에 띄워 둔 개발 서버를 끄지 않고 돌린다.
 * 🚨 호스트는 `localhost` 하나로 맞춘다. refresh 쿠키가 host-only 라서 `127.0.0.1` 과 섞으면 안 실린다.
 */
export const WEB_PORT = 3100;
export const API_PORT = 8100;
export const WEB_ORIGIN = `http://localhost:${WEB_PORT}`;
export const API_ORIGIN = `http://localhost:${API_PORT}`;

export const API_DIR = path.resolve(__dirname, "../../api");

/**
 * api 의 환경변수. `apps/api/.env` 위에 덮는다 (pydantic-settings 는 환경변수가 `.env` 를 이긴다).
 *
 * DB 접속(DB_HOST · DB_PORT · DB_USER · DB_PASSWORD)은 덮지 않는다 — 로컬은 `.env` 의 개발 DB 서버,
 * CI 는 워크플로가 준 값을 그대로 쓴다. **DB 이름만** 따로 둔다. 테스트가 보호자와 아이를 계속
 * 만들어서, 개발 DB 에 섞이면 손으로 보는 화면이 지저분해진다.
 *
 * 🚨 **밖으로 나가는 키는 전부 비운다.** LLM · 임베딩 · 외부 공공 API · 카카오 · 푸시 · 알림이다.
 *    1단계 테스트는 LLM 을 부르지 않는다. 실수로 부르는 화면에 닿아도 키가 없으니 밖으로 못 나가고
 *    "쓸 수 없어요" 로 끝난다. `apps/api/.env` 에 실제 키가 있는 로컬에서도 같다.
 */
export function apiEnv(): Record<string, string> {
  const blank = (keys: string[]) => Object.fromEntries(keys.map((key) => [key, ""]));
  const roles = ["MEMORY", "SUPERVISOR", "FOOD", "ACTIVITY", "GROWTH", "HEALTH"];
  return {
    APP_ENV: "local",
    DB_NAME: process.env.E2E_DB_NAME ?? "icatch_e2e",
    CORS_ALLOW_ORIGINS: WEB_ORIGIN,
    AUTH_RETURN_URL_WEB: `${WEB_ORIGIN}/auth/callback`,
    AUTH_RETURN_URL_APP: "icatch://auth",
    PUSH_DEVICE_ACCEPT: "false",
    ...blank(roles.flatMap((role) => [`${role}_API_KEY`, `${role}_BASE_URL`])),
    ...blank(["EMBEDDING_API_KEY", "EMBEDDING_BASE_URL"]),
    ...blank(["CURATOR_JUDGE_API_KEY", "CURATOR_JUDGE_BASE_URL"]),
    ...blank(["MEAL_OCR_API_KEY", "MEAL_OCR_BASE_URL"]),
    ...blank([
      "DATA4LIBRARY_AUTH_KEY",
      "DATA_GO_KR_SERVICE_KEY",
      "MAFRA_RECIPE_API_KEY",
      "NICS_RECIPE_API_KEY",
    ]),
    ...blank(["KAKAO_REST_API_KEY", "KAKAO_CLIENT_SECRET", "KAKAO_ADMIN_KEY"]),
    ...blank(["FCM_SERVICE_ACCOUNT_FILE", "ALERT_WEBHOOK_URL", "REDIS_URL"]),
  };
}
