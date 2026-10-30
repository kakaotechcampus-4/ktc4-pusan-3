/**
 * 말로 적기 판정 — 웹 메시지 검사 · 기기 안 인식 조건 · 오류 코드 → 이유 · 웹에 넣는 스크립트 (#595).
 *
 * 🚨 비행기 모드에서도 글자가 나오는지(= 기기 안 인식)는 여기서 못 본다. Android 13+ 기기에서 본다.
 *
 * 실행: `pnpm test`
 */

import assert from "node:assert/strict";
import test from "node:test";

import { isInternalUrlOf } from "./internal-url.ts";
import {
  canListenOnDevice,
  parseWebRequest,
  permissionReason,
  speechBridgeScript,
  speechErrorReason,
  speechEventScript,
  volumeLevel,
} from "./speech.ts";

const ORIGIN = "https://icatch.ai.kr";
/** 셸이 쓰는 것과 같은 판정 (`config.ts` 의 `isInternalUrl`). */
const isInternal = (url: string) => isInternalUrlOf(url, ORIGIN);
/** 🚨 Android 웹뷰는 보낸 쪽을 경로 없이 출처만 넘긴다 (`RNCWebView` 의 `sourceOrigin`). */
const PAGE = ORIGIN;

test("우리 페이지가 보낸 시작 · 멈춤만 받는다", () => {
  assert.deepEqual(parseWebRequest('{"kind":"speech","action":"start"}', PAGE, isInternal), {
    kind: "speech",
    action: "start",
  });
  assert.deepEqual(parseWebRequest('{"kind":"speech","action":"stop"}', PAGE, isInternal), {
    kind: "speech",
    action: "stop",
  });
});

test("경로까지 붙은 주소로 와도 우리 출처면 받는다", () => {
  assert.deepEqual(
    parseWebRequest('{"kind":"speech","action":"start"}', `${ORIGIN}/child/c_1/home`, isInternal),
    { kind: "speech", action: "start" },
  );
});

test("남의 페이지가 보낸 것은 모양이 맞아도 버린다", () => {
  for (const sender of [
    "https://evil.example",
    "https://icatch.ai.kr.evil.example",
    "https://sub.icatch.ai.kr",
    "http://icatch.ai.kr",
    "https://icatch.ai.kr:8443",
    "about:blank",
    "data:text/html,x",
    "",
  ]) {
    assert.equal(
      parseWebRequest('{"kind":"speech","action":"start"}', sender, isInternal),
      null,
      sender,
    );
  }
});

test("모양이 어긋나면 던지지 않고 버린다", () => {
  for (const data of [
    "",
    "not json",
    "null",
    "42",
    '"speech"',
    "[]",
    '{"kind":"speech"}',
    '{"kind":"speech","action":"record"}',
    '{"kind":"location","action":"start"}',
    `{"kind":"speech","action":"start","pad":"${"x".repeat(300)}"}`,
  ]) {
    assert.equal(parseWebRequest(data, PAGE, isInternal), null, data);
  }
});

test("기기 안 인식이 보장되는 Android 13+ 에서만 켠다", () => {
  assert.equal(canListenOnDevice({ os: "android", apiLevel: 33, onDevice: true }), true);
  // 12 이하는 오프라인 힌트뿐이라 서버로 갈 수 있다.
  assert.equal(canListenOnDevice({ os: "android", apiLevel: 32, onDevice: true }), false);
  assert.equal(canListenOnDevice({ os: "android", apiLevel: 35, onDevice: false }), false);
  // iOS 는 기기 안이 안 되면 조용히 서버로 돈다 — 확인 전까지 끈다.
  assert.equal(canListenOnDevice({ os: "ios", apiLevel: 0, onDevice: true }), false);
});

test("권한은 다시 물을 수 있으면 declined, 없으면 denied 다", () => {
  assert.equal(permissionReason({ granted: true, canAskAgain: true }), null);
  assert.equal(permissionReason({ granted: false, canAskAgain: true }), "declined");
  assert.equal(permissionReason({ granted: false, canAskAgain: false }), "denied");
});

test("소리 크기는 0~1 로 자르고 소수 둘째 자리까지만 넘긴다", () => {
  assert.equal(volumeLevel(-2), 0);
  assert.equal(volumeLevel(0), 0);
  assert.equal(volumeLevel(3.456), 0.35);
  assert.equal(volumeLevel(10), 1);
  assert.equal(volumeLevel(14), 1);
  assert.equal(volumeLevel(Number.NaN), 0);
});

test("오류 코드는 웹이 아는 이유로 바꾼다", () => {
  assert.equal(speechErrorReason("not-allowed"), "denied");
  assert.equal(speechErrorReason("service-not-allowed"), "unsupported");
  assert.equal(speechErrorReason("no-speech"), "no-speech");
  assert.equal(speechErrorReason("speech-timeout"), "no-speech");
  assert.equal(speechErrorReason("network"), "failed");
  assert.equal(speechErrorReason("busy"), "failed");
});

test("셸이 멈춘 것과 모델이 없는 것은 웹에 바로 알리지 않는다", () => {
  assert.equal(speechErrorReason("aborted"), null);
  assert.equal(speechErrorReason("language-not-supported"), null);
});

test("창구 스크립트는 지원 여부를 그대로 싣는다", () => {
  assert.match(speechBridgeScript(true), /available: true,/);
  assert.match(speechBridgeScript(false), /available: false,/);
});

test("이벤트 스크립트는 따옴표가 섞인 글자도 깨지지 않는다", () => {
  const script = speechEventScript({ type: "result", text: 'a"b\'c</script>', final: true });
  const dispatched: unknown[] = [];
  const window = {
    dispatchEvent: (event: { detail: unknown }) => dispatched.push(event.detail),
  };
  class CustomEvent {
    detail: unknown;
    constructor(_name: string, init: { detail: unknown }) {
      this.detail = init.detail;
    }
  }
  new Function("window", "CustomEvent", script)(window, CustomEvent);
  assert.deepEqual(dispatched, [{ type: "result", text: 'a"b\'c</script>', final: true }]);
});
