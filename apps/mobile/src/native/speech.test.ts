/**
 * 말로 적기 판정 — 웹 메시지 검사 · 기기 안 인식 조건 · 오류 코드 → 이유 · 웹에 넣는 스크립트 (#595).
 *
 * 🚨 비행기 모드에서도 글자가 나오는지(= 기기 안 인식)는 여기서 못 본다. Android 13+ 기기에서 본다.
 *
 * 실행: `pnpm test`
 */

import assert from "node:assert/strict";
import test from "node:test";

import {
  canListenOnDevice,
  parseWebRequest,
  speechBridgeScript,
  speechErrorReason,
  speechEventScript,
} from "./speech.ts";

const ORIGIN = "https://icatch.ai.kr";
const isInternal = (url: string) => url.startsWith(`${ORIGIN}/`);
const PAGE = `${ORIGIN}/child/c_1/home`;

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

test("남의 페이지가 보낸 것은 모양이 맞아도 버린다", () => {
  assert.equal(
    parseWebRequest('{"kind":"speech","action":"start"}', "https://evil.example/", isInternal),
    null,
  );
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
