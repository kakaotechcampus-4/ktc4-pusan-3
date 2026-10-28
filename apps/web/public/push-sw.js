/**
 * 일정 알림 웹 푸시 서비스 워커 (#609).
 *
 * 🚨 **Firebase 를 불러오지 않는다.** FCM 은 웹 푸시(Push API)로 도착하고, 받는 것은 이 워커가
 *    직접 한다. Firebase 의 워커 SDK 를 쓰면 알림을 누를 때 SDK 가 클릭을 가로채고
 *    (`stopImmediatePropagation`), 메시지에 `fcm_options.link` 가 없으면 아무 데도 열지 않는다
 *    (`@firebase/messaging` 0.13.3 `onNotificationClick`). 서버는 웹 주소를 싣지 않고 `data` 만 싣는다
 *    (#610) — 라우트는 웹이 안다. 그래서 띄우기와 누르기를 여기서 한다. Firebase 는 페이지에서
 *    토큰을 받을 때만 쓴다 (`src/lib/push/web-push.ts`).
 * 🚨 **이 워커는 페이지를 다스리지 않는다.** 범위가 `/push/` 다 (`web-push.ts`). 범위를 `/` 로 두면
 *    개발 환경의 목 워커(`mockServiceWorker.js`)와 같은 자리를 다퉈 한쪽이 밀려난다. 푸시와 알림
 *    클릭은 범위와 상관없이 이 워커로 온다.
 * 🚨 **탭을 보고 있어도 알림을 띄운다.** 화면 안에서 따로 알리려면 토스트를 써야 하는데, 토스트는
 *    "조용히 되돌아간 실패" 에만 쓴다 (apps/web/CLAUDE.md §3).
 * 🚨 **로그에 아무것도 남기지 않는다.** 알림 문구와 data 에 아이 · 일정 id 가 있다.
 *
 * 푸시 본문은 FCM 이 보내는 모양 그대로다: `{ notification: { title, body }, data: { … } }`.
 * data 의 값은 모두 문자열이다 — 서버 `app/workers/reminder_dispatch.py` 의 `reminder_message` (#610).
 */

const CHILD_ID = /^[A-Za-z0-9_-]{1,64}$/;
const DATE = /^\d{4}-\d{2}-\d{2}$/;

/**
 * 알림을 누르면 열 화면. 그 아이의 그날 09 캘린더다.
 * 🚨 data 를 그대로 주소에 붙이지 않는다 — 모양을 본 값만 쓰고, 아니면 첫 화면이다.
 */
function targetPath(data) {
  if (
    data &&
    data.type === "schedule_reminder" &&
    CHILD_ID.test(data.child_id ?? "") &&
    DATE.test(data.date ?? "")
  ) {
    return `/child/${data.child_id}/calendar?date=${data.date}`;
  }
  return "/";
}

// 새 워커가 기다리지 않고 바로 받게 한다. 이 워커는 페이지를 다스리지 않아서 바꿔 끼워도 화면이 안 바뀐다.
self.addEventListener("install", () => {
  self.skipWaiting();
});

self.addEventListener("push", (event) => {
  let payload = null;
  try {
    payload = event.data ? event.data.json() : null;
  } catch {
    payload = null;
  }
  if (!payload || !payload.notification) return;

  const { title, body } = payload.notification;
  event.waitUntil(
    self.registration.showNotification(title || "아이캐치", {
      body: body || "",
      data: { path: targetPath(payload.data) },
    }),
  );
});

/**
 * 🚨 **열린 탭이 있으면 새 탭을 띄우지 않는다.** 그 탭을 앞으로 가져오고 갈 곳을 메시지로 보낸다 —
 *    이 워커가 다스리지 않는 탭이라 `navigate()` 를 부를 수 없다. 탭은 `WebPushBridge` 가 받아 옮긴다.
 *    열린 탭이 없을 때만 새로 연다.
 */
self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const path = (event.notification.data && event.notification.data.path) || "/";

  event.waitUntil(
    (async () => {
      const windows = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
      const tab = windows.find((client) => new URL(client.url).origin === self.location.origin);
      if (tab) {
        await tab.focus();
        tab.postMessage({ type: "icatch:push-open", path });
        return;
      }
      await self.clients.openWindow(path);
    })(),
  );
});
