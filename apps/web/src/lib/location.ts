/**
 * 흐린 위치 — 좌표를 얻는 **유일한 곳** (#593).
 *
 * 놀이 추천이 근처 공원 · 도서관을 찾을 수 있게, 보호자가 허용하면 입력 요청에 **약 1km 로 흐린 좌표**를 싣는다
 * (`InputLocation` · 서버 `CoarseLocation`). 앱 안이면 셸 창구(`lib/native/location.ts`), 브라우저면
 * `navigator.geolocation`(`enableHighAccuracy: false`)이다.
 *
 * 🚨 **어느 쪽이든 여기서 소수점 둘째 자리로 줄인다** (`coarseLocation`). 셸이 이미 줄여 보내지만 다시 줄인다 —
 *    밖으로 나가는 좌표가 이 함수를 거쳤다는 것만 보면 되게 한다. 서버도 한 번 더 줄인다.
 * 🚨 **좌표를 남기지 않는다.** 웹 저장소(`localStorage` · 영속 스토어) · 콘솔 · 에러 리포트 어디에도.
 *    읽은 값은 그 입력 요청 하나에 싣고 끝이다 (`stores/conversation.ts` 가 다시 시도용으로 메모리에만 붙든다).
 * 🚨 **권한 창은 `askDeviceLocation` 만 띄운다.** 가입 화면과 10 설정에서 위치 동의를 켰을 때다.
 *    입력을 보낼 때(`readCoarseLocation`)는 묻지 않는다 — 이미 허용한 휴대폰에서만 읽는다.
 *
 * 화면이 그리는 규칙은 `docs/web/auth-consent-v1.md` 의 "위치" 절이다.
 */

import type { InputLocation } from "@/lib/api/types";
import {
  LOCATION_EVENT,
  readLocationEvent,
  shellLocation,
  type LocationBridge,
  type ShellLocationEvent,
} from "@/lib/native/location";

/** 남기는 소수 자리. 위도 0.01° 는 약 1.1km 다. 🚨 서버 `COARSE_DECIMALS` · 셸 `COARSE_DECIMALS` 와 같은 값이다. */
export const COARSE_DECIMALS = 2;

/**
 * 입력을 보낼 때 좌표를 기다리는 시간. 넘으면 위치 없이 보낸다.
 * 셸은 마지막으로 알려진 위치부터 보고(대개 즉답), 브라우저는 10분 안의 값이면 다시 재지 않는다.
 */
export const READ_TIMEOUT_MS = 2_000;
/** 권한 상태만 묻는 시간. 셸이 이 기능 전 버전이면 답이 오지 않는다. */
const CHECK_TIMEOUT_MS = 2_000;
/** 권한 창에 답하는 시간. 보호자가 고르는 중이라 넉넉히 둔다. */
const ASK_TIMEOUT_MS = 120_000;
/** 브라우저가 이만큼 지난 위치까지는 다시 재지 않는다. 셸의 `LAST_KNOWN_MAX_AGE_MS` 와 같다. */
const MAX_AGE_MS = 10 * 60 * 1000;

/**
 * 이 휴대폰(브라우저)의 위치 권한 상태.
 * - `granted` — 허용했다. 입력에 좌표를 싣는다
 * - `prompt` — 허용 전이고 물을 수 있다
 * - `declined` — 방금 권한 창에서 거절했다. 다시 물을 수 있다 (앱만 · 브라우저는 거절하면 `denied` 다)
 * - `denied` — 거절했고 더 물을 수 없다. 휴대폰(브라우저) 설정에서만 켤 수 있다
 * - `unsupported` — 위치를 읽을 길이 없다 (위치 모듈이 없는 앱 · geolocation 이 없는 브라우저 · 셸이 답하지 않음)
 */
export type DeviceLocationPermission = "granted" | "prompt" | "declined" | "denied" | "unsupported";

/**
 * 새로 본 권한 상태를 앞의 상태에 얹는다.
 * 🚨 방금 거절한(`declined`) 뒤의 확인(`check`)은 `prompt` 로 온다 — OS 가 "한 번 거절함" 과 "안 물어봄" 을 가르지 않는다.
 *    권한 창이 닫히며 화면이 다시 보일 때 확인이 돌아서, 그대로 받으면 "허용하지 않았어요" 가 "꺼져 있어요" 로 바뀌었다 (#593 실기기).
 */
export function nextPermission(
  previous: DeviceLocationPermission | null,
  next: DeviceLocationPermission,
): DeviceLocationPermission {
  return previous === "declined" && next === "prompt" ? previous : next;
}

/** 좌표 하나를 `COARSE_DECIMALS` 자리로 줄인다. 서버와 같이 반올림이다. */
export function coarsen(value: number): number {
  const scale = 10 ** COARSE_DECIMALS;
  return Math.round(value * scale) / scale || 0;
}

/**
 * 좌표 → 서버에 보낼 흐린 좌표. 숫자가 아니거나 범위를 벗어나면 `null` (싣지 않는다).
 * 🚨 서버로 가는 좌표는 반드시 이 함수를 거친다 (머리말).
 */
export function coarseLocation(lat: number, lon: number): InputLocation | null {
  if (!Number.isFinite(lat) || !Number.isFinite(lon)) return null;
  if (Math.abs(lat) > 90 || Math.abs(lon) > 180) return null;
  return { lat: coarsen(lat), lon: coarsen(lon) };
}

/** 권한 상태만 본다. 🚨 묻지 않고 좌표도 읽지 않는다 — 10 설정이 안내를 고를 때 쓴다. */
export async function checkDeviceLocation(): Promise<DeviceLocationPermission> {
  const shell = shellLocation();
  if (shell)
    return askShell(shell, "check", permissionOf("check"), CHECK_TIMEOUT_MS, "unsupported");
  if (!canUseBrowserLocation()) return "unsupported";
  return (await browserPermission()) ?? "unsupported";
}

/**
 * 허용 전이고 물을 수 있으면 권한 창을 띄운다. 이미 허용했으면 묻지 않고 `granted`.
 * 🚨 위치 동의를 켠 직후에만 부른다 (가입 화면 · 10 설정). 좌표는 서버로 보내지 않는다.
 */
export async function askDeviceLocation(): Promise<DeviceLocationPermission> {
  const shell = shellLocation();
  if (shell) return askShell(shell, "ask", permissionOf("ask"), ASK_TIMEOUT_MS, "unsupported");
  // 🚨 권한 상태를 볼 수 없는 브라우저는 묻지 않는다 — 허용받아도 입력을 보낼 때 확인할 수 없어 싣지 못한다.
  if (!canUseBrowserLocation() || (await browserPermission()) === null) return "unsupported";
  // 브라우저에는 "권한만 묻기" 가 없다. 위치를 한 번 읽어 권한 창을 띄우고, 읽은 좌표는 버린다.
  return new Promise((resolve) => {
    navigator.geolocation.getCurrentPosition(
      () => resolve("granted"),
      (error) => {
        if (error.code === error.PERMISSION_DENIED) resolve("denied");
        // 허용은 했는데 못 읽은 것(시간 초과 · 위치 꺼짐)이면 권한 상태를 따로 본다.
        else void browserPermission().then((state) => resolve(state ?? "unsupported"));
      },
      { enableHighAccuracy: false, maximumAge: MAX_AGE_MS, timeout: ASK_TIMEOUT_MS },
    );
  });
}

/**
 * 입력에 실을 흐린 좌표. 허용하지 않았거나 못 읽었거나 `READ_TIMEOUT_MS` 를 넘기면 `null` — 위치 없이 보낸다.
 * 🚨 **묻지 않는다.** 브라우저는 권한이 `granted` 로 확인될 때만 읽는다 — 그렇지 않으면 `getCurrentPosition` 이
 *    권한 창을 띄운다. 권한 상태를 볼 수 없는 브라우저(Permissions API 없음)는 읽지 않는다.
 */
export async function readCoarseLocation(): Promise<InputLocation | null> {
  const shell = shellLocation();
  if (shell) {
    return askShell(
      shell,
      "read",
      (event) => {
        if (event.type === "position") return coarseLocation(event.lat, event.lon);
        if (event.type === "unavailable") return null;
        return undefined;
      },
      READ_TIMEOUT_MS,
      null,
    );
  }
  if (!canUseBrowserLocation() || (await browserPermission()) !== "granted") return null;
  return new Promise((resolve) => {
    navigator.geolocation.getCurrentPosition(
      ({ coords }) => resolve(coarseLocation(coords.latitude, coords.longitude)),
      () => resolve(null),
      { enableHighAccuracy: false, maximumAge: MAX_AGE_MS, timeout: READ_TIMEOUT_MS },
    );
  });
}

/**
 * 이 앱의 휴대폰 설정 화면을 연다. 앱 안에서만 된다 — 브라우저는 사이트 설정을 열 길이 없어서 `false`.
 * OS 가 더 묻지 못하게 막은(`denied`) 보호자가 직접 켜는 길이다.
 */
export function openDeviceLocationSettings(): boolean {
  const shell = shellLocation();
  if (!shell) return false;
  shell.openSettings();
  return true;
}

/** 지금 앱 셸 안인가 — 안내 문구가 "휴대폰 설정" 인지 "브라우저 설정" 인지 고른다. */
export function inLocationShell(): boolean {
  return shellLocation() !== null;
}

/* ── 셸 · 브라우저 ────────────────────────────────────────────────────── */

/** 그 부탁(`check` · `ask`)의 답만 받는다. 다른 부탁의 답은 `undefined` — 계속 기다린다. */
function permissionOf(action: "check" | "ask") {
  return (event: ShellLocationEvent): DeviceLocationPermission | undefined =>
    event.type === "permission" && event.action === action ? event.status : undefined;
}

/**
 * 셸에 부탁 하나를 보내고 맞는 답 하나를 기다린다. 답이 없으면 `timeoutMs` 뒤에 `fallback`.
 * `pick` 이 `undefined` 를 돌려주면 이 부탁의 답이 아니다 — 다른 부탁의 답이라 계속 기다린다.
 */
function askShell<T>(
  bridge: LocationBridge,
  action: "check" | "ask" | "read",
  pick: (event: ShellLocationEvent) => T | undefined,
  timeoutMs: number,
  fallback: T,
): Promise<T> {
  return new Promise((resolve) => {
    const finish = (value: T) => {
      window.removeEventListener(LOCATION_EVENT, onEvent);
      clearTimeout(timer);
      resolve(value);
    };
    const onEvent = (event: Event) => {
      const parsed = readLocationEvent((event as CustomEvent<unknown>).detail);
      if (!parsed) return;
      const value = pick(parsed);
      if (value !== undefined) finish(value);
    };
    window.addEventListener(LOCATION_EVENT, onEvent);
    const timer = setTimeout(() => finish(fallback), timeoutMs);
    bridge[action]();
  });
}

/**
 * 브라우저 길을 쓸 수 있는가.
 * 🚨 **앱 셸 안이면 쓰지 않는다** — 위치 창구가 없거나 꺼진 앱(이 기능 전 버전 · 위치 모듈이 없는 빌드)이다. 웹뷰는
 *    `geolocationEnabled` 가 꺼져 있어서 `navigator.geolocation` 이 거절로 끝나고, 앱 안에 "브라우저 설정" 안내가 섰다.
 * 🚨 셸은 `window.ReactNativeWebView` 로 안다 — 셸이 웹뷰에 `onMessage` 를 달면 생긴다.
 */
function canUseBrowserLocation(): boolean {
  if (typeof window !== "undefined" && "ReactNativeWebView" in window) return false;
  return typeof navigator !== "undefined" && "geolocation" in navigator;
}

/** 브라우저의 위치 권한 상태. 볼 수 없으면 `null`. */
async function browserPermission(): Promise<"granted" | "prompt" | "denied" | null> {
  try {
    const status = await navigator.permissions?.query({ name: "geolocation" });
    return status?.state ?? null;
  } catch {
    return null;
  }
}
