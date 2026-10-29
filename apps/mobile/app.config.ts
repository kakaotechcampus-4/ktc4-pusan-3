import { existsSync } from "node:fs";
import { join } from "node:path";

import type { ConfigContext, ExpoConfig } from "expo/config";

/**
 * `app.json` 위에 **빌드할 때만 정해지는 것**을 얹는다. 나머지 설정은 그대로 `app.json` 이 정본이다.
 *
 * `google-services.json` — 일정 알림의 FCM 설정 (#609 FE-4).
 * 🚨 **저장소에 두지 않는다** (`.gitignore`). EAS 빌드는 파일 환경변수 `GOOGLE_SERVICES_JSON`(EAS 가 내려 준
 *    파일 경로)을, 로컬 개발 빌드는 이 폴더의 `google-services.json` 을 쓴다.
 * 🚨 **없으면 넣지 않는다** — 알림만 꺼진 빌드가 된다. 셸이 이 값이 실렸는지 보고 웹에 창구를 꺼서 낸다
 *    (`App.tsx` 의 `PUSH_AVAILABLE`). 웹 푸시의 "Firebase 값이 없으면 꺼짐" 과 같다. 있다고 적어 놓고 파일이
 *    없으면 prebuild 가 멈춘다.
 *
 * ⚠️ Node 에서 도는 파일이라 앱 타입(`tsconfig.json`)이 아니라 `tsconfig.test.json` 으로 검사한다.
 */
export default ({ config }: ConfigContext): ExpoConfig => {
  const local = join(__dirname, "google-services.json");
  const googleServicesFile =
    process.env.GOOGLE_SERVICES_JSON ?? (existsSync(local) ? "./google-services.json" : undefined);

  return {
    ...config,
    name: config.name ?? "아이캐치",
    slug: config.slug ?? "icatch",
    android: { ...config.android, ...(googleServicesFile ? { googleServicesFile } : {}) },
  };
};
