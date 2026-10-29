/**
 * 말로 적기 — **음성은 휴대폰 밖으로 나가지 않는다** (#595).
 *
 * 웹의 채팅바 마이크가 셸에 "듣기 시작" · "그만" 을 부탁하면, 셸이 기기 안 음성인식을 돌리고
 * **나온 글자만** 웹에 넘긴다. 글자는 보호자가 직접 친 것과 같은 길(입력창 → 보내기)로 간다.
 *
 * 🚨 **기기 안 인식이 보장될 때만 켠다.** 음성을 서비스 밖으로 보내는 것은 정한 적이 없다 (#595 · 외부 전달
 *    범위는 최상위 CLAUDE.md §10 의 미정이고, 개인정보 처리방침의 국외 이전 표에 음성인식 업체가 없다).
 *    그래서 "기기 안이 안 되면 서버 인식으로" 같은 대체 경로를 두지 않는다 — 버튼을 끄고 이유를 말한다.
 *    `expo-speech-recognition` 의 `requiresOnDeviceRecognition` 이 그것을 보장하는 범위는 좁다.
 *    - Android 13(API 33)+ — `SpeechRecognizer.createOnDeviceSpeechRecognizer` 를 쓴다. 보장된다.
 *    - Android 12 이하 — `EXTRA_PREFER_OFFLINE` 힌트만 붙이고 기본 인식기를 쓴다. **서버로 갈 수 있다.**
 *    - iOS — 기기 안 지원 여부를 한국어가 아니라 기기 기본 언어로 판정하고, 지원하지 않으면 그 옵션을
 *      **조용히 빼고** 서버 인식으로 돈다. 기기에서 확인하기 전까지 끈다.
 *
 * 🚨 **인식된 글자를 로그에 남기지 않는다** (최상위 CLAUDE.md §2 개인정보). 아이 얘기 원문이다.
 *
 * 받는 쪽은 `apps/web/src/lib/native/speech.ts` 다. 이름(`icatch:speech` · 메시지 모양)을 바꾸려면
 * **양쪽을 같이** 고친다.
 */

import type { PushAction } from "./push";

/** 웹에 결과를 알리는 이벤트 이름. */
const EVENT = "icatch:speech";

/** 이보다 긴 메시지는 볼 것도 없이 버린다. 지금 오는 메시지는 수십 자다. */
const MAX_MESSAGE_LENGTH = 200;

/** 인식 언어. 바꾸면 Android 의 기기 안 모델도 그 언어로 받아야 한다. */
export const SPEECH_LANG = "ko-KR";

/** Android 13 — 기기 안 인식기를 따로 만들 수 있는 첫 버전 (`createOnDeviceSpeechRecognizer`). */
const ON_DEVICE_MIN_API = 33;

/**
 * 웹이 셸에 하는 부탁. 🚨 이 모양이 아니면 네이티브를 부르지 않는다 (`apps/mobile/CLAUDE.md` §4).
 * `push` 는 일정 알림이다 (#609 · `push.ts`).
 */
export type WebRequest =
  | { kind: "speech"; action: "start" | "stop" }
  | { kind: "push"; action: PushAction };

/**
 * 웹 → 셸 메시지를 검사한다. 통과하지 못하면 `null` 이고, 호출부는 아무것도 하지 않는다.
 *
 * 🚨 **보낸 쪽 주소를 먼저 본다.** 웹뷰가 앱 밖 페이지를 띄우는 일은 막아 뒀지만(`isInternalUrl`),
 *    그 문이 한 번 뚫려도 남의 페이지가 이 휴대폰의 마이크를 켜면 안 된다.
 * 🚨 **던지지 않는다.** 모양이 어긋난 메시지 하나로 셸이 죽으면 안 된다.
 */
export function parseWebRequest(
  data: string,
  senderUrl: string,
  isInternal: (url: string) => boolean,
): WebRequest | null {
  if (!isInternal(senderUrl)) return null;
  if (data.length > MAX_MESSAGE_LENGTH) return null;
  let value: unknown;
  try {
    value = JSON.parse(data);
  } catch {
    return null;
  }
  if (typeof value !== "object" || value === null) return null;
  const { kind, action } = value as Record<string, unknown>;
  if (kind === "speech" && (action === "start" || action === "stop")) return { kind, action };
  if (kind === "push" && (action === "ready" || action === "sync" || action === "ask")) {
    return { kind, action };
  }
  return null;
}

/**
 * 이 휴대폰에서 말로 적기를 켤 수 있는가 — **기기 안 인식이 보장되는 곳만** (머리말).
 *
 * `onDevice` 는 `supportsOnDeviceRecognition()` 이다. 한국어 모델이 깔려 있는지는 여기서 모른다 —
 * 시작해 봐야 알고, 없으면 `language-not-supported` 로 온다 (아래 `speechErrorReason`).
 */
export function canListenOnDevice({
  os,
  apiLevel,
  onDevice,
}: {
  os: string;
  apiLevel: number;
  onDevice: boolean;
}): boolean {
  return os === "android" && apiLevel >= ON_DEVICE_MIN_API && onDevice;
}

/**
 * 웹에 알리는 것. 웹은 이 여섯만 안다.
 *
 * - `asking` — 곧 시스템 권한 창이 뜬다. 웹이 왜 마이크가 필요한지 한 줄로 먼저 말한다. Android 권한 창에는
 *   우리 문장이 나오지 않는다 (`app.json` 의 문구는 iOS 전용이다 · 정보통신망법 제22조의2 선택 접근권한 고지)
 * - `volume` — 들리는 소리의 크기 0~1. 웹의 음량 막대가 쓴다
 * - `result` 의 `final` 이 거짓이면 아직 말하는 중이다 — 웹은 같은 자리의 글자를 바꿔 끼운다.
 */
export type SpeechEvent =
  | { type: "asking" }
  | { type: "start" }
  | { type: "volume"; level: number }
  | { type: "result"; text: string; final: boolean }
  | { type: "end" }
  | { type: "error"; reason: SpeechErrorReason };

/**
 * 웹이 안내를 고르는 이유. 인식기의 오류 코드를 그대로 넘기지 않는다 — 웹이 Android 를 알 필요가 없다.
 *
 * - `declined` — 이번에 거절했다. 다시 누르면 권한 창이 한 번 더 뜬다
 * - `denied` — 마이크 권한을 받지 못했고 다시 물을 수 없다. 설정에서 켜야 한다
 * - `unsupported` — 이 휴대폰은 기기 안 인식을 못 한다
 * - `preparing` — 한국어 모델이 없어서 받기 시작했다. 받은 뒤 다시 누르면 된다
 * - `no-speech` — 아무 말도 듣지 못했다
 * - `failed` — 그 밖
 */
export type SpeechErrorReason =
  | "declined"
  | "denied"
  | "unsupported"
  | "preparing"
  | "no-speech"
  | "failed";

/**
 * 권한 응답 → 웹에 넘길 이유. 받았으면 `null`.
 *
 * Android 는 처음 거절한 뒤에는 다시 물을 수 있고(`canAskAgain`), 두 번 거절하면 설정에서만 켤 수 있다.
 * 두 경우에 같은 말("설정에서 켜 주세요")을 하면 다시 누르기만 하면 될 보호자를 설정 화면으로 보낸다.
 */
export function permissionReason(response: {
  granted: boolean;
  canAskAgain: boolean;
}): SpeechErrorReason | null {
  if (response.granted) return null;
  return response.canAskAgain ? "declined" : "denied";
}

/**
 * 인식기의 소리 크기(`volumechange` 의 -2~10 · 0 아래는 들리지 않는 것) → 웹에 넘길 0~1.
 * 소수 둘째 자리에서 자른다 — 0.1초마다 가는 값이라 짧을수록 좋고, 막대 다섯 개에 그 이상은 보이지 않는다.
 */
export function volumeLevel(value: number): number {
  if (!Number.isFinite(value)) return 0;
  const level = Math.min(Math.max(value / 10, 0), 1);
  return Math.round(level * 100) / 100;
}

/**
 * 인식기 오류 코드 → 웹에 넘길 이유. `null` 이면 웹에 알리지 않는다.
 *
 * - `aborted` 는 셸이 스스로 멈춘 것이다 (백그라운드 · 웹의 "그만"). 이어서 오는 `end` 로 충분하다.
 * - `language-not-supported` 는 여기서 정하지 않는다. 셸이 모델 받기를 걸어 보고 그 결과로
 *   `preparing` 이나 `unsupported` 를 고른다.
 */
export function speechErrorReason(code: string): SpeechErrorReason | null {
  switch (code) {
    case "aborted":
    case "language-not-supported":
      return null;
    case "not-allowed":
      return "denied";
    case "service-not-allowed":
      return "unsupported";
    case "no-speech":
    case "speech-timeout":
      return "no-speech";
    default:
      return "failed";
  }
}

/**
 * 웹이 문서를 읽기 **전에** 꽂는 창구 (`window.icatch.speech`).
 *
 * `available` 은 셸이 정한다 (`canListenOnDevice`). 거짓이어도 창구는 꽂는다 — 웹은 창구가 **없으면**
 * "앱에서 쓸 수 있어요", **있는데 거짓이면** "이 휴대폰에서는 못 써요" 로 다르게 말한다.
 *
 * 🚨 `window.ReactNativeWebView` 는 웹뷰에 `onMessage` 가 있어야 생긴다.
 */
export function speechBridgeScript(available: boolean): string {
  return `(function () {
  window.icatch = window.icatch || {};
  if (window.icatch.speech) return;
  function send(action) {
    if (!window.ReactNativeWebView) return;
    window.ReactNativeWebView.postMessage(JSON.stringify({ kind: "speech", action: action }));
  }
  window.icatch.speech = {
    available: ${available ? "true" : "false"},
    start: function () { send("start"); },
    stop: function () { send("stop"); },
  };
})();
true;`;
}

/** 셸 → 웹 이벤트 하나를 넣는 스크립트. 글자는 `JSON.stringify` 로만 싣는다 — 따옴표가 섞여도 깨지지 않는다. */
export function speechEventScript(event: SpeechEvent): string {
  return `window.dispatchEvent(new CustomEvent(${JSON.stringify(EVENT)},{detail:${JSON.stringify(event)}}));true;`;
}
