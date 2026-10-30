/**
 * 일정 알림 — 앱에서 푸시를 받는 창구 (#609 FE-4).
 *
 * 웹뷰에는 Push API 가 없어서 웹이 스스로 FCM 토큰을 받지 못한다. 그래서 **셸이 기기의 FCM 토큰을 받아
 * 웹에 넘기고, 등록(`PUT /me/push-device`)은 웹이 한다** — 셸은 API 를 부르지 않는다 (CLAUDE.md §1).
 * 받은 알림을 띄우는 것은 OS(FCM)가 하고, 누르면 셸이 그 화면 주소를 웹에 넘긴다.
 *
 * 🚨 **권한은 웹이 부탁할 때만 묻는다** — 알림을 고를 때다 (`ask`). 앱을 켤 때는 이미 허용한
 *    휴대폰만 토큰을 넘긴다 (`sync`). OS 가 더 묻지 못하게 막으면 그만 묻는다 (`shouldAskPermission`).
 * 🚨 **토큰을 로그에 남기지 않는다.** 셸 → 웹 이벤트에만 싣는다 (`JSON.stringify`).
 * 🚨 **알림의 data 를 주소에 그대로 쓰지 않는다.** 모양을 본 값만 쓴다 — 웹의 `public/push-sw.js` 와 같은 규칙이다.
 *
 * 웹 쪽 짝은 `apps/web/src/lib/native/push.ts` 다. 결정과 이유는 `docs/web/web-push-v1.md` "앱".
 */

const EVENT = "icatch:push";

/** 알림 채널. 🚨 `app.json` 의 `expo-notifications` 플러그인 `defaultChannel` 과 같은 값이다. */
export const PUSH_CHANNEL_ID = "schedule";
/** 휴대폰 설정 › 알림에 보이는 이름. */
export const PUSH_CHANNEL_NAME = "일정 알림";

/**
 * 셸이 웹에 보내는 것.
 * - `token` — 이 휴대폰의 FCM 토큰. 웹이 `platform: "android"` 로 등록한다
 * - `open` — 알림을 눌렀다. 웹이 그 화면으로 옮긴다
 */
export type PushEvent = { type: "token"; token: string } | { type: "open"; path: string };

/** 웹이 셸에 하는 부탁. `ready` 는 "이제 `open` 을 받을 수 있다" 다 (앱을 알림으로 연 경우). */
export type PushAction = "ready" | "sync" | "ask";

const CHILD_ID = /^[A-Za-z0-9_-]{1,64}$/;
const DATE = /^\d{4}-\d{2}-\d{2}$/;

/**
 * 알림의 data → 열 화면. 그 아이의 그날 09 캘린더다. 모양이 다르면 `null` (앱만 연다).
 * 서버는 `type` · `child_id` · `event_id` · `date` 를 문자열로 싣는다 (#610 `reminder_message`).
 */
export function pushOpenPath(data: unknown): string | null {
  if (typeof data !== "object" || data === null) return null;
  const { type, child_id: childId, date } = data as Record<string, unknown>;
  if (type !== "schedule_reminder") return null;
  if (typeof childId !== "string" || !CHILD_ID.test(childId)) return null;
  if (typeof date !== "string" || !DATE.test(date)) return null;
  return `/child/${childId}/calendar?date=${date}`;
}

/**
 * 알림을 누른 응답에서 data 를 꺼낸다. 앱이 꺼져 있거나 뒤에 있을 때 FCM 이 띄운 알림은 data 가
 * `content.data` 가 아니라 원래 메시지(`trigger.remoteMessage.data`)에만 있을 수 있어서 둘 다 본다.
 */
export function responseData(response: {
  notification: { request: { content: { data?: unknown }; trigger?: unknown } };
}): unknown {
  const { content, trigger } = response.notification.request;
  if (content.data && typeof content.data === "object" && Object.keys(content.data).length > 0) {
    return content.data;
  }
  const remote = (trigger as { remoteMessage?: { data?: unknown } } | null | undefined)
    ?.remoteMessage;
  return remote?.data ?? null;
}

/**
 * 권한 창을 띄울 것인가 — **아직 허용 전이고 OS 가 다시 물을 수 있을 때.**
 *
 * 🚨 **`status === "undetermined"` 로 판정하지 않는다.** Android 13+ 의 `expo-notifications` 는 한 번도 묻지
 *    않았어도 알림이 꺼져 있으면 `"denied"` 를 돌려준다(`NotificationPermissionsModule.kt` 의 `!areEnabled`).
 *    그 판정으로는 권한 창이 영영 뜨지 않았다 (#609 실기기 확인 · SM-S942N Android 17).
 * 믿을 수 있는 것은 `canAskAgain` 이다. 한 번도 안 물었을 때와 한 번 거절했을 때 참이고, 두 번 거절하면 OS 가
 * 거짓으로 막는다. 그래서 알림을 고를 때 많아야 두 번 묻고, 한 번 거절한 보호자도 다시 켤 길이 있다.
 */
export function shouldAskPermission(permission: { status: string; canAskAgain: boolean }): boolean {
  return permission.status !== "granted" && permission.canAskAgain;
}

/**
 * 문서가 뜨기 전에 `window.icatch.push` 를 꽂는다. 🚨 이미 있으면 덮지 않는다(새로고침 없이 다시 주입될 때).
 */
export function pushBridgeScript(available: boolean): string {
  return `(function () {
  window.icatch = window.icatch || {};
  if (window.icatch.push) return;
  function send(action) {
    if (!window.ReactNativeWebView) return;
    window.ReactNativeWebView.postMessage(JSON.stringify({ kind: "push", action: action }));
  }
  window.icatch.push = {
    available: ${available ? "true" : "false"},
    ready: function () { send("ready"); },
    sync: function () { send("sync"); },
    ask: function () { send("ask"); },
  };
})();
true;`;
}

/** 셸 → 웹 이벤트. 🚨 값은 `JSON.stringify` 로만 싣는다 — 문자열을 이어 붙이면 토큰 한 글자로 스크립트가 깨진다. */
export function pushEventScript(event: PushEvent): string {
  return `window.dispatchEvent(new CustomEvent(${JSON.stringify(EVENT)},{detail:${JSON.stringify(event)}}));true;`;
}
