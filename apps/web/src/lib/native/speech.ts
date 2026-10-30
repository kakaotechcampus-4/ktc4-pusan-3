/**
 * 말로 적기 — **네이티브 셸이 꽂아 주는 창구**의 웹 쪽 절반 (#595).
 *
 * 🚨 **음성은 휴대폰 밖으로 나가지 않는다.** 셸이 기기 안 음성인식을 돌리고 **글자만** 넘긴다.
 *    음성을 서비스 밖으로 보내는 것은 정한 적이 없다 (#595 · 처리방침의 국외 이전 표에 음성인식 업체가 없다). 그래서 브라우저의
 *    `SpeechRecognition` 을 쓰지 않는다 — Chrome · Safari 는 음성을 Google · Apple 서버로 보낸다.
 *    Android 웹뷰에는 그것조차 없다. 기기 안 인식이 보장되는 브라우저에서 여는 것은 #598 이다.
 *
 * 🚨 **셸이 없으면(브라우저) 마이크를 끄고 이유를 말한다.** 창구가 있는데 `available` 이 거짓이면
 *    이 휴대폰이 기기 안 인식을 못 하는 것이다 (`apps/mobile/src/native/speech.ts`).
 *
 * 🚨 **들은 글자는 입력창에 넣기만 한다.** 보내기는 보호자가 누른다 — 잘못 알아들은 말이 그대로
 *    아이 기록이 되면 안 된다.
 *
 * 🚨 **글자를 콘솔 · 로그에 찍지 않는다** (최상위 CLAUDE.md §2 개인정보). 아이 얘기 원문이다.
 *
 * 보내는 쪽은 `apps/mobile/src/native/speech.ts` 다. 이름(`icatch:speech` · 이벤트 모양)을 바꾸려면
 * **양쪽을 같이** 고친다.
 */

/** 셸이 이벤트를 쏘는 이름. */
export const SPEECH_EVENT = "icatch:speech";

/** 셸이 `window.icatch.speech` 에 꽂는 것. */
export interface SpeechBridge {
  /** 이 휴대폰에서 기기 안 인식을 쓸 수 있는가. 셸이 앱을 켤 때 정한다. */
  available: boolean;
  /** 듣기 시작. 권한은 이때 묻는다. 결과는 `SPEECH_EVENT` 로 온다. */
  start(): void;
  /**
   * 그만 듣기. 끝은 `end` 로 온다. 그 앞에 말하던 구간이 `final` 로 한 번 더 오기도 하고 안 오기도 한다 —
   * Google 의 기기 안 인식기는 `final` 대신 오류로 끝낸다(셸이 그 오류를 넘기지 않는다). 그래서 웹은 `final` 이
   * 아닌 글자도 입력창에 바로 쓴다.
   */
  stop(): void;
}

declare global {
  interface IcatchShell {
    speech?: SpeechBridge;
  }
}

/**
 * 웹이 안내를 고르는 이유. 셸이 인식기 오류 코드와 권한 응답을 이 여섯으로 줄여 보낸다.
 * `declined` 는 다시 누르면 권한 창이 한 번 더 뜨고, `denied` 는 설정에서만 켤 수 있다.
 */
export type SpeechErrorReason =
  "declined" | "denied" | "unsupported" | "preparing" | "no-speech" | "failed";

/**
 * 셸이 알리는 것.
 *
 * - `asking` — 곧 시스템 권한 창이 뜬다. 왜 마이크가 필요한지 먼저 말한다 (`SPEECH_ASKING`).
 *   Android 권한 창에는 우리 문장이 나오지 않는다 (정보통신망법 제22조의2 선택 접근권한 고지).
 * - `volume` — 들리는 소리의 크기 0~1. 음량 막대가 쓴다.
 * - `result` 의 `final` 이 거짓이면 아직 말하는 중이다 — 같은 자리의 글자를 바꿔 끼운다.
 *   참이면 그 구간이 끝났다 — 다음 구간은 그 뒤에 붙는다.
 */
export type SpeechEvent =
  | { type: "asking" }
  | { type: "start" }
  | { type: "volume"; level: number }
  | { type: "result"; text: string; final: boolean }
  | { type: "end" }
  | { type: "error"; reason: SpeechErrorReason };

/** 권한 창이 뜨기 직전에 하는 말. 🚨 음성이 밖으로 나가지 않는다는 것까지 말한다 — 그게 이 권한의 조건이다. */
export const SPEECH_ASKING =
  "말한 내용을 휴대폰 안에서 글자로 바꾸려고 마이크를 써요. 음성은 휴대폰 밖으로 보내지 않아요.";

/** 이유마다 보호자에게 하는 말. 🚨 화면 글자라 기호 · 줄표를 쓰지 않는다 (apps/web/CLAUDE.md §5). */
export const SPEECH_NOTICE: Record<SpeechErrorReason, string> = {
  declined: "마이크를 허용해야 말로 적기를 쓸 수 있어요. 다시 누르면 한 번 더 물어요.",
  denied: "마이크 권한이 꺼져 있어요. 휴대폰 설정에서 아이캐치의 마이크를 켜 주세요.",
  unsupported: "이 휴대폰에서는 말로 적기를 쓸 수 없어요. 글로 적어 주세요.",
  preparing: "한국어 음성 인식을 휴대폰에 내려받고 있어요. 조금 뒤에 다시 눌러 주세요.",
  "no-speech": "말소리를 듣지 못했어요. 다시 눌러서 말해 주세요.",
  failed: "말로 적기를 시작하지 못했어요. 다시 눌러 주세요.",
};

/**
 * 이 화면에서 말로 적기가 어떤 상태인가.
 *
 * - `none` — 셸 밖(브라우저). 서버 렌더링도 여기다
 * - `unavailable` — 셸 안이지만 이 휴대폰은 기기 안 인식을 못 한다
 * - `ready` — 쓸 수 있다
 */
export type SpeechSupport = "none" | "unavailable" | "ready";

export function getSpeechBridge(): SpeechBridge | undefined {
  return typeof window === "undefined" ? undefined : window.icatch?.speech;
}

export function readSpeechSupport(): SpeechSupport {
  const bridge = getSpeechBridge();
  if (!bridge) return "none";
  return bridge.available ? "ready" : "unavailable";
}

const ERROR_REASONS: readonly string[] = [
  "declined",
  "denied",
  "unsupported",
  "preparing",
  "no-speech",
  "failed",
] satisfies SpeechErrorReason[];

/** 셸이 보낸 값이 약속한 모양인가. 🚨 어긋나면 버린다 — 입력창에 `undefined` 가 찍히면 안 된다. */
export function isSpeechEvent(value: unknown): value is SpeechEvent {
  if (typeof value !== "object" || value === null) return false;
  const event = value as Record<string, unknown>;
  switch (event.type) {
    case "asking":
    case "start":
    case "end":
      return true;
    case "volume":
      return typeof event.level === "number" && event.level >= 0 && event.level <= 1;
    case "result":
      return typeof event.text === "string" && typeof event.final === "boolean";
    case "error":
      return typeof event.reason === "string" && ERROR_REASONS.includes(event.reason);
    default:
      return false;
  }
}

/** 셸 이벤트를 받는다. 돌려준 함수로 끊는다. */
export function subscribeSpeech(handler: (event: SpeechEvent) => void): () => void {
  const listener = (e: Event) => {
    const detail = (e as CustomEvent<unknown>).detail;
    if (isSpeechEvent(detail)) handler(detail);
  };
  window.addEventListener(SPEECH_EVENT, listener);
  return () => window.removeEventListener(SPEECH_EVENT, listener);
}

/**
 * 입력창의 글 뒤에 들은 말을 붙인다. 글이 공백으로 끝나지 않으면 한 칸 띄운다 — 붙여 쓰면 앞 낱말과
 * 한 낱말이 된다. 들은 말이 비었으면 글을 그대로 둔다.
 */
export function appendSpoken(base: string, spoken: string): string {
  const words = spoken.trim();
  if (!words) return base;
  if (!base || /\s$/.test(base)) return base + words;
  return `${base} ${words}`;
}
