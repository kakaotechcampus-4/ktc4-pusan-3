/**
 * 위치 — 놀이 추천이 근처 공원 · 도서관을 찾을 때 쓰는 **약 1km 로 흐린 좌표** (#593).
 *
 * 웹이 부탁하면 셸이 대략 위치(`ACCESS_COARSE_LOCATION`)를 읽어 **소수점 둘째 자리로 자른 값만** 웹에 넘긴다.
 * 좌표를 입력 요청에 싣는 것은 웹이다 — 셸은 API 를 부르지 않는다 (CLAUDE.md §1).
 *
 * 🚨 **원좌표는 셸 밖으로 나가지 않는다.** 위치 약관 제4조 ② 가 "휴대폰 안에서 약 1km 로 흐리게 바꾼 뒤 전달" 을
 *    약속했다. 웹으로 가는 길은 `locationEventScript` 하나고, 거기 싣는 값은 `coarsePosition` 을 거친 것뿐이다.
 * 🚨 **웹뷰의 `navigator.geolocation` 길을 쓰지 않는다** (`geolocationEnabled` 를 켜지 않는다).
 *    react-native-webview 13.16.1 은 그 요청을 정밀 위치 권한(`ACCESS_FINE_LOCATION`)으로만 확인하고 요청해서
 *    (`RNCWebChromeClient.onGeolocationPermissionsShowPrompt`) 정밀 위치를 선언해야 하고, 원좌표가 웹 JS 까지 올라온다.
 * 🚨 **좌표를 로그에 남기지 않는다.** 저장하지도 않는다 — 부탁 한 번에 읽고 넘기고 끝이다.
 *
 * 받는 쪽은 `apps/web/src/lib/native/location.ts` 다. 이름(`icatch:location` · 메시지 모양)을 바꾸려면
 * **양쪽을 같이** 고친다.
 */

const EVENT = "icatch:location";

/** 남기는 소수 자리. 위도 0.01° 는 약 1.1km 다. 🚨 서버 `CoarseLocation` 의 `COARSE_DECIMALS` 와 같은 값이다. */
export const COARSE_DECIMALS = 2;

/**
 * 웹이 셸에 하는 부탁.
 * - `check` — 권한 상태만 알려 달라. 🚨 **묻지 않고 좌표도 읽지 않는다** — 10 설정이 안내를 고를 때 쓴다
 * - `ask` — 허용 전이고 OS 가 다시 물을 수 있으면 권한 창을 띄우고, 결과를 알려 달라. 좌표는 읽지 않는다.
 *   가입 화면과 10 설정에서 위치 동의를 켰을 때다
 * - `read` — 이미 허용한 휴대폰이면 좌표를 달라. 🚨 **묻지 않는다** — 입력을 보낼 때 부른다
 * - `settings` — 이 앱의 휴대폰 설정 화면을 열어 달라. OS 가 더 묻지 못하게 막은(`denied`) 보호자가 직접 켜는 길이다.
 *   답은 없다 — 돌아오면 웹이 `check` 로 다시 본다
 */
export type LocationAction = "check" | "ask" | "read" | "settings";

/**
 * 셸이 웹에 보내는 것. 부탁 하나에 하나가 온다.
 * - `permission` — `check` · `ask` 의 답. 🚨 어느 부탁의 답인지(`action`)를 싣는다 — 권한 창이 떠 있는 동안 10 설정이
 *   `check` 를 보내면 그 답이 `ask` 의 답으로 잡혀, 거절을 "허용 전" 으로 말했다 (#593 실기기)
 * - `position` — `read` 의 답. 흐린 좌표
 * - `unavailable` — `read` 에 좌표가 없다. 이유는 `LocationUnavailableReason`
 */
export type LocationEvent =
  | { type: "permission"; action: "check" | "ask"; status: LocationPermissionStatus }
  | { type: "position"; lat: number; lon: number }
  | { type: "unavailable"; reason: LocationUnavailableReason };

/**
 * 권한 상태. 웹이 안내를 고르는 데 쓴다.
 * - `granted` — 허용했다
 * - `prompt` — 허용 전이고 물을 수 있다. 묻지 않은 `check` 의 답이다 (한 번도 안 물었는지 한 번 거절했는지는 OS 가 가르지 않는다)
 * - `declined` — 방금 권한 창에서 거절했다. 다시 물을 수 있다
 * - `denied` — 거절했고 OS 가 더 묻지 못하게 막았다. 휴대폰 설정에서만 켤 수 있다
 * - `unsupported` — 이 빌드에 위치 모듈이 없다
 */
export type LocationPermissionStatus = "granted" | "prompt" | "declined" | "denied" | "unsupported";

/**
 * `read` 에 좌표가 없는 이유.
 * - `not-granted` — 허용하지 않았다 (`prompt` · `denied` · 모듈 없음). 묻지 않았다
 * - `failed` — 허용은 됐는데 읽지 못했다 (위치 서비스 꺼짐 · 모듈 오류)
 */
export type LocationUnavailableReason = "not-granted" | "failed";

/** 좌표 하나를 `COARSE_DECIMALS` 자리로 줄인다. 서버와 같이 반올림이다 (`round`). */
export function coarsen(value: number): number {
  const scale = 10 ** COARSE_DECIMALS;
  // `-0` 이 `JSON.stringify` 에서 `0` 이 되긴 하지만 테스트에서 헷갈리지 않게 지운다.
  return Math.round(value * scale) / scale || 0;
}

/**
 * 기기가 준 좌표 → 웹에 넘길 흐린 좌표. 숫자가 아니거나 범위를 벗어나면 `null` (넘기지 않는다).
 * 🚨 셸 → 웹으로 가는 좌표는 반드시 이 함수를 거친다 (머리말).
 */
export function coarsePosition(coords: {
  latitude: number;
  longitude: number;
}): { lat: number; lon: number } | null {
  const { latitude, longitude } = coords;
  if (!Number.isFinite(latitude) || !Number.isFinite(longitude)) return null;
  if (Math.abs(latitude) > 90 || Math.abs(longitude) > 180) return null;
  return { lat: coarsen(latitude), lon: coarsen(longitude) };
}

/**
 * 권한 응답 → 웹에 넘길 상태. `asked` 는 이번에 권한 창을 띄웠는가다.
 * 🚨 묻지 않은 `check` 의 "허용 전" 을 `declined` 로 말하지 않는다 — 보호자가 거절했다고 단정하게 된다.
 * 🚨 `status === "undetermined"` 로 가르지 않는다 — 믿을 수 있는 것은 `granted` 와 `canAskAgain` 이다 (`push.ts` 의 `shouldAskPermission`).
 */
export function permissionStatus(
  permission: { granted: boolean; canAskAgain: boolean },
  asked: boolean,
): LocationPermissionStatus {
  if (permission.granted) return "granted";
  if (!permission.canAskAgain) return "denied";
  return asked ? "declined" : "prompt";
}

/**
 * 문서가 뜨기 전에 `window.icatch.location` 을 꽂는다. 🚨 이미 있으면 덮지 않는다.
 * `available` 이 거짓이면(위치 모듈이 없는 빌드) 웹은 위치 없이 보낸다.
 */
export function locationBridgeScript(available: boolean): string {
  return `(function () {
  window.icatch = window.icatch || {};
  if (window.icatch.location) return;
  function send(action) {
    if (!window.ReactNativeWebView) return;
    window.ReactNativeWebView.postMessage(JSON.stringify({ kind: "location", action: action }));
  }
  window.icatch.location = {
    available: ${available ? "true" : "false"},
    check: function () { send("check"); },
    ask: function () { send("ask"); },
    read: function () { send("read"); },
    openSettings: function () { send("settings"); },
  };
})();
true;`;
}

/** 셸 → 웹 이벤트. 값은 `JSON.stringify` 로만 싣는다. */
export function locationEventScript(event: LocationEvent): string {
  return `window.dispatchEvent(new CustomEvent(${JSON.stringify(EVENT)},{detail:${JSON.stringify(event)}}));true;`;
}
