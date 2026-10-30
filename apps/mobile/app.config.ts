import { existsSync } from "node:fs";
import { join } from "node:path";

import type { ConfigContext, ExpoConfig } from "expo/config";
import { AndroidConfig, withAndroidManifest } from "expo/config-plugins";

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
 * FCM 자동 등록을 끈다 (#618 리뷰) — 매니페스트 `firebase_messaging_auto_init_enabled = false`.
 * 🚨 **끄지 않으면 이 파일이 든 빌드는 앱을 켜자마자 FCM 에 기기를 등록한다.** Firebase 는 auto-init 이 기본으로
 *    켜져 있어서(`FirebaseMessaging$AutoInit` — 앱 저장값 → 이 메타데이터 → 데이터 수집 기본값 순으로 본다) 알림을
 *    고른 적 없는 보호자도 Google 에 등록되고, 그 토큰이 `addPushTokenListener` 로 웹에 넘어가 권한 허용 없이
 *    서버에 등록될 수 있다. 토큰은 셸이 `getDevicePushTokenAsync`(= `getToken()` · auto-init 을 보지 않는다)를
 *    부를 때만 만든다 — 권한을 허용한 뒤다 (`App.tsx` 의 `handOverPushToken`).
 *    ⚠️ 끄면 앱을 켤 때 토큰을 미리 새로 고치지 않는다. 웹이 앱을 켤 때마다 `sync` 로 다시 받아 가서
 *    (`getToken()` 이 오래된 토큰을 바꿔 준다) 그 몫을 한다.
 *
 * ⚠️ Node 에서 도는 파일이라 앱 타입(`tsconfig.json`)이 아니라 `tsconfig.test.json` 으로 검사한다.
 */
export default ({ config }: ConfigContext): ExpoConfig => {
  const local = join(__dirname, "google-services.json");
  const googleServicesFile =
    process.env.GOOGLE_SERVICES_JSON ?? (existsSync(local) ? "./google-services.json" : undefined);

  return withFcmAutoInitOff({
    ...config,
    name: config.name ?? "아이캐치",
    slug: config.slug ?? "icatch",
    android: { ...config.android, ...(googleServicesFile ? { googleServicesFile } : {}) },
  });
};

/** 매니페스트 `<application>` 에 FCM auto-init 끄기를 넣는다 (위 머리말). */
const withFcmAutoInitOff = (config: ExpoConfig): ExpoConfig =>
  withAndroidManifest(config, (mod) => {
    AndroidConfig.Manifest.addMetaDataItemToMainApplication(
      AndroidConfig.Manifest.getMainApplicationOrThrow(mod.modResults),
      "firebase_messaging_auto_init_enabled",
      "false",
    );
    return mod;
  });
