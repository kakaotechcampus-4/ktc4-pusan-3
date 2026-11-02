/**
 * 앱 셸이 꽂는 위치 창구 — 웹 쪽 약속 (#593).
 *
 * 웹뷰의 `navigator.geolocation` 은 쓰지 않는다. react-native-webview 가 그 요청을 정밀 위치 권한으로만 받아서,
 * 셸이 대략 위치를 직접 읽고 **소수점 둘째 자리로 흐린 값만** `icatch:location` 이벤트로 넘긴다.
 * 셸 쪽 짝은 `apps/mobile/src/native/location.ts`. 이름과 모양을 바꾸려면 **양쪽을 같이** 고친다.
 *
 * 🚨 창구가 없으면(브라우저 · 이 기능 전의 앱) 브라우저 길로 간다 (`lib/location.ts`).
 */

/** 셸이 이벤트를 쏘는 이름. */
export const LOCATION_EVENT = "icatch:location";

/** 셸이 `window.icatch.location` 에 꽂는 것. 답은 전부 `LOCATION_EVENT` 로 온다. */
export interface LocationBridge {
  /** 이 앱에 위치 모듈이 들어 있는가. */
  available: boolean;
  /** 권한 상태만 알려 달라. 🚨 묻지 않고 좌표도 읽지 않는다. 답은 `permission`. */
  check(): void;
  /** 허용 전이고 OS 가 다시 물을 수 있으면 권한 창을 띄운다. 답은 `permission`. 좌표는 읽지 않는다. */
  ask(): void;
  /** 이미 허용했으면 흐린 좌표를 달라. 🚨 묻지 않는다. 답은 `position` 이나 `unavailable`. */
  read(): void;
  /** 이 앱의 휴대폰 설정 화면을 연다. 답은 없다 — 돌아오면 `check` 로 다시 본다. */
  openSettings(): void;
}

/** 셸의 `LocationPermissionStatus` 와 같다. */
export type ShellLocationPermission = "granted" | "prompt" | "declined" | "denied" | "unsupported";

/**
 * 셸 → 웹. 모양은 셸의 `LocationEvent` 와 같다.
 * 🚨 `permission` 은 어느 부탁(`check` · `ask`)의 답인지 싣는다 — 권한 창이 떠 있는 동안 온 `check` 의 답을 `ask` 의 답으로 받지 않는다.
 */
export type ShellLocationEvent =
  | { type: "permission"; action: "check" | "ask"; status: ShellLocationPermission }
  | { type: "position"; lat: number; lon: number }
  | { type: "unavailable"; reason: "not-granted" | "failed" };

declare global {
  interface IcatchShell {
    location?: LocationBridge;
  }
}

const PERMISSIONS: ReadonlySet<string> = new Set([
  "granted",
  "prompt",
  "declined",
  "denied",
  "unsupported",
]);

/** 지금 앱 셸 안이고 위치 창구가 켜져 있으면 그 창구. 아니면 `null`. */
export function shellLocation(): LocationBridge | null {
  if (typeof window === "undefined") return null;
  const bridge = window.icatch?.location;
  return bridge?.available ? bridge : null;
}

/**
 * 셸이 보낸 이벤트를 읽는다. 모양이 다르면 `null`.
 * 🚨 셸이 넣는 값이지만 다시 본다 — 이 이벤트 이름은 페이지 안의 누구든 쏠 수 있다. 좌표는 숫자이고 범위 안이어야 한다.
 *    흐리는 것은 받는 쪽(`lib/location.ts`)이 한 번 더 한다.
 */
export function readLocationEvent(detail: unknown): ShellLocationEvent | null {
  if (typeof detail !== "object" || detail === null) return null;
  const event = detail as Record<string, unknown>;
  if (event.type === "permission" && typeof event.status === "string") {
    if (event.action !== "check" && event.action !== "ask") return null;
    return PERMISSIONS.has(event.status)
      ? {
          type: "permission",
          action: event.action,
          status: event.status as ShellLocationPermission,
        }
      : null;
  }
  if (event.type === "position") {
    const { lat, lon } = event;
    if (typeof lat !== "number" || typeof lon !== "number") return null;
    if (!Number.isFinite(lat) || !Number.isFinite(lon)) return null;
    if (Math.abs(lat) > 90 || Math.abs(lon) > 180) return null;
    return { type: "position", lat, lon };
  }
  if (
    event.type === "unavailable" &&
    (event.reason === "not-granted" || event.reason === "failed")
  ) {
    return { type: "unavailable", reason: event.reason };
  }
  return null;
}
