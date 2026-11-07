import { isPushOpenPath } from "@/lib/push/open-path";

/**
 * 앱 셸이 꽂는 일정 알림 창구 — 웹 쪽 약속 (#609 FE-4).
 *
 * 웹뷰에는 Push API 가 없어서 앱에서는 웹이 스스로 FCM 토큰을 받지 못한다. 셸이 기기의 토큰을 받아
 * `icatch:push` 이벤트로 넘기고, **등록(`PUT /me/push-device`)은 웹이 한다** — 셸은 API 를 부르지 않는다.
 * 알림을 누르면 셸이 그 화면 주소를 같은 이벤트로 넘긴다. 셸 쪽 짝은 `apps/mobile/src/native/push.ts`.
 *
 * 🚨 창구가 없으면(브라우저 · 이 기능 전의 앱 · iOS) 웹은 브라우저 웹 푸시 길로 간다 (`lib/push/web-push.ts`).
 */

/** 셸이 이벤트를 쏘는 이름. */
export const PUSH_EVENT = "icatch:push";

/** 셸이 `window.icatch.push` 에 꽂는 것. */
export interface PushBridge {
  /** 이 앱에서 알림을 받을 수 있는가. Android 이고 알림 모듈이 들어간 빌드다. */
  available: boolean;
  /** 이제 알림을 누른 화면 주소를 받을 수 있다. 앱이 알림으로 열렸으면 그 주소가 이때 온다. */
  ready(): void;
  /** 이미 허용한 휴대폰이면 토큰을 보내 달라. 🚨 묻지 않는다. */
  sync(): void;
  /** 허용 전이고 OS 가 다시 물을 수 있으면 권한을 묻고, 허용하면 토큰을 보내 달라. 판단은 셸이 한다. */
  ask(): void;
}

/** 셸 → 웹. 모양은 셸의 `PushEvent` 와 같다. */
export type PushEvent = { type: "token"; token: string } | { type: "open"; path: string };

declare global {
  interface IcatchShell {
    push?: PushBridge;
  }
}

/** 지금 앱 셸 안이고 알림 창구가 켜져 있으면 그 창구. 아니면 `null`. */
export function shellPush(): PushBridge | null {
  if (typeof window === "undefined") return null;
  const bridge = window.icatch?.push;
  return bridge?.available ? bridge : null;
}

/**
 * 셸이 보낸 이벤트를 읽는다. 모양이 다르면 `null`.
 * 🚨 셸이 넣는 값이지만 다시 본다 — 이 이벤트 이름은 페이지 안의 누구든 쏠 수 있다. 특히 `open` 의 주소는
 *    알림으로 열어도 되는 모양(`isPushOpenPath`)만 받는다 — 그 주소가 그대로 `router.push` 로 간다.
 */
export function readPushEvent(detail: unknown): PushEvent | null {
  if (typeof detail !== "object" || detail === null) return null;
  const event = detail as Record<string, unknown>;
  if (event.type === "token" && typeof event.token === "string" && event.token !== "") {
    return { type: "token", token: event.token };
  }
  if (event.type === "open" && typeof event.path === "string" && isPushOpenPath(event.path)) {
    return { type: "open", path: event.path };
  }
  return null;
}
