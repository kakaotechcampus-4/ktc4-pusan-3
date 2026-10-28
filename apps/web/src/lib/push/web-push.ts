import { api } from "@/lib/api/client";
import type { PushDeviceRequest } from "@/lib/api/types";
import { FIREBASE_WEB } from "@/lib/env";
import { NO_REMINDER, type ReminderChoice } from "@/lib/reminder";
// ⚠️ `lib/` 에서 스토어를 읽는 드문 자리다. `lib/api/client.ts` 는 서버 컴포넌트에서도 쓰여서 스토어를 안 읽지만,
//    이 파일은 브라우저 API 를 쓰는 클라이언트 전용이라(`ReminderField` · `WebPushBridge` 만 부른다) 그 제약이 없다.
import { hasLiveSession, useSessionStore } from "@/stores/session";

/**
 * 브라우저 웹 푸시 — 권한 묻기, FCM 토큰 받기, 이 로그인의 기기로 등록하기 (#609).
 *
 * 🚨 **Firebase 는 토큰을 받을 때만 쓴다.** 받은 푸시를 띄우고 누르는 것은 `public/push-sw.js` 가
 *    Firebase 없이 한다 (그 파일 머리말). Firebase 는 등록할 때(허용한 브라우저에서 앱을 켤 때마다)
 *    동적으로 불러온다 — 첫 화면 번들에 들어가지 않고, 웹 푸시가 꺼진 환경에서는 아예 받지 않는다.
 * 🚨 **기기는 로그인에 묶인다** (#605). 로그인이 만료되면 서버가 기기를 지우므로, 앱을 켤 때와
 *    로그인 직후마다 다시 등록한다(`syncWebPush`). 토큰이 바뀌어도 그때 새 토큰이 나간다.
 *    로그아웃 때 따로 지우지 않는다 — 서버가 로그인과 함께 지운다.
 * 🚨 **권한은 앱을 열자마자 묻지 않는다.** 처음 알림을 고를 때 묻는다(`shouldAskWebPush` → `askWebPush`).
 *    무엇에 쓰는지 보이는 순간이라야 허용할 이유가 있다. 브라우저는 한 번 거절하면 다시 묻지 못한다.
 * 🚨 **실패해도 화면을 막지 않는다.** 알림은 덤이다 — 등록이 안 됐다고 일정 제출이 실패하지 않는다.
 * 🚨 **이 코드는 토큰을 로그 · 웹 저장소 · 에러 리포트에 쓰지 않는다.** 같은 토큰을 다시 보내지 않으려고
 *    메모리에만 든다. ⚠️ Firebase SDK 는 브라우저 단위로 IndexedDB 에 토큰 정보를 둔다 — 로그아웃 때
 *    `deleteToken` 을 부르지 않는 이유는 `docs/web/web-push-v1.md` "토큰이 남는 곳".
 * 🚨 **앱 셸(안드로이드 웹뷰)에서는 동작하지 않는다.** 웹뷰에는 Push API 가 없어 `unavailable` 이고,
 *    앱의 토큰은 셸이 넘긴다(#609 FE-4).
 */

export const PUSH_SW_URL = "/push-sw.js";
/** 🚨 `/` 가 아니다 — 개발 환경의 목 워커와 자리를 다투지 않게 (`push-sw.js` 머리말). */
export const PUSH_SW_SCOPE = "/push/";

/** 권한 창의 상태. `unavailable` 은 설정값이 없거나 이 브라우저가 웹 푸시를 못 받는 것이다. */
export type WebPushPermission = "unavailable" | NotificationPermission;

export function webPushPermission(): WebPushPermission {
  if (FIREBASE_WEB === null || typeof window === "undefined") return "unavailable";
  if (
    !("serviceWorker" in navigator) ||
    !("PushManager" in window) ||
    !("Notification" in window)
  ) {
    return "unavailable";
  }
  return Notification.permission;
}

/** 이번 로그인에 등록한 토큰. 같은 토큰을 화면마다 다시 보내지 않는다. 🚨 이 코드는 메모리에만 둔다. */
let registeredToken: string | null = null;
let inFlight: Promise<void> | null = null;

/** 로그아웃하면 잊는다 — 다음 로그인은 새 기기 행이라 다시 보내야 한다. */
export function forgetWebPushRegistration(): void {
  registeredToken = null;
}

/**
 * 알림을 고른 순간 권한을 물을 것인가. 🚨 **"없음" 을 고를 때는 묻지 않는다** — 무엇에 쓰는지 안 보이는 때다.
 * 이미 답한 브라우저 · 웹 푸시가 꺼진 환경 · 앱 셸에서도 묻지 않는다.
 */
export function shouldAskWebPush(choice: ReminderChoice): boolean {
  return choice !== NO_REMINDER && webPushPermission() === "default";
}

/** 앱을 켤 때 · 로그인 직후. 🚨 **묻지 않는다** — 이미 허용한 브라우저만 등록한다. */
export function syncWebPush(): Promise<void> {
  if (webPushPermission() !== "granted") return Promise.resolve();
  return register();
}

/**
 * 처음 알림을 고를 때 권한을 묻고, 허용하면 등록한다.
 * 🚨 **클릭 핸들러 안에서 바로 부른다.** 브라우저는 사용자가 누른 직후에만 권한 창을 띄운다 —
 *    그래서 `requestPermission` 이 이 함수의 첫 일이다.
 */
export function askWebPush(): Promise<void> {
  if (webPushPermission() !== "default") return Promise.resolve();
  return Notification.requestPermission().then((permission) =>
    permission === "granted" ? register() : undefined,
  );
}

function register(): Promise<void> {
  // 화면이 동시에 두 번 불러도(앱 켤 때 + 알림 고르기) 토큰 받기는 한 번이다.
  inFlight ??= registerOnce().finally(() => {
    inFlight = null;
  });
  return inFlight;
}

async function registerOnce(): Promise<void> {
  const config = FIREBASE_WEB;
  if (config === null) return;
  const [{ getApps, initializeApp }, { getMessaging, getToken, isSupported }] = await Promise.all([
    import("firebase/app"),
    import("firebase/messaging"),
  ]);
  if (!(await isSupported())) return;

  const registration = await navigator.serviceWorker.register(PUSH_SW_URL, {
    scope: PUSH_SW_SCOPE,
  });
  // 🚨 SDK 는 넘겨받은 등록이 켜질 때까지 기다리지 않는다(자기가 만든 기본 등록만 기다린다).
  //    켜지기 전에 구독하면 실패한다. `navigator.serviceWorker.ready` 는 쓸 수 없다 — 이 워커는
  //    페이지를 다스리지 않아서 그 약속이 끝나지 않는다.
  await whenActive(registration);

  const { apiKey, projectId, messagingSenderId, appId, vapidKey } = config;
  const app = getApps()[0] ?? initializeApp({ apiKey, projectId, messagingSenderId, appId });
  const token = await getToken(getMessaging(app), {
    vapidKey,
    serviceWorkerRegistration: registration,
  });
  if (!token || token === registeredToken) return;
  // 🚨 토큰을 받는 사이(처음엔 몇 초) 로그아웃했으면 보내지 않는다. 토큰 없이 나간 요청이 401 을 맞으면
  //    세션 만료 처리가 한 번 더 돈다.
  if (!hasLiveSession(useSessionStore.getState())) return;

  await api.put<void>("/me/push-device", {
    token,
    platform: "web",
  } satisfies PushDeviceRequest);
  registeredToken = token;
}

const ACTIVATE_TIMEOUT_MS = 10_000;

function whenActive(registration: ServiceWorkerRegistration): Promise<void> {
  if (registration.active) return Promise.resolve();
  const incoming = registration.installing ?? registration.waiting;
  if (!incoming) return Promise.reject(new Error("push-sw 가 설치되지 않았다"));
  return new Promise((resolve, reject) => {
    const timer = setTimeout(
      () => reject(new Error("push-sw 가 켜지지 않았다")),
      ACTIVATE_TIMEOUT_MS,
    );
    incoming.addEventListener("statechange", () => {
      if (incoming.state === "activated") {
        clearTimeout(timer);
        resolve();
      }
    });
  });
}

/* ── 알림을 눌렀을 때 ─────────────────────────────────────────────────── */

/** `push-sw.js` 의 `targetPath` 가 만드는 모양. 🚨 둘을 같이 고친다. */
const OPEN_PATH = /^\/$|^\/child\/[A-Za-z0-9_-]{1,64}\/calendar\?date=\d{4}-\d{2}-\d{2}$/;

/**
 * 워커가 열린 탭에 보낸 "여기로 가 주세요" 를 읽는다. 아니면 `null`.
 * 🚨 **모양을 다시 본다.** 이 페이지에는 목 워커도 메시지를 보낸다 — 우리 것만, 우리 주소만 받는다.
 */
export function pushOpenPath(message: unknown): string | null {
  if (typeof message !== "object" || message === null) return null;
  const { type, path } = message as { type?: unknown; path?: unknown };
  if (type !== "icatch:push-open" || typeof path !== "string") return null;
  return OPEN_PATH.test(path) ? path : null;
}
