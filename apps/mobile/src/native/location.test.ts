/**
 * 흐린 위치 판정 — 웹 부탁 검사 · 좌표 흐리기 · 권한 응답 → 이유 · 웹에 넣는 스크립트 (#593).
 *
 * 🚨 실제 권한 창("대략적인 위치" 만 뜨는지)과 매니페스트 권한은 여기서 못 본다. Android 개발 빌드에서 본다.
 *
 * 실행: `pnpm test`
 */

import assert from "node:assert/strict";
import { runInNewContext } from "node:vm";
import test from "node:test";

import { isInternalUrlOf } from "./internal-url.ts";
import {
  coarsePosition,
  coarsen,
  locationBridgeScript,
  locationEventScript,
  permissionStatus,
} from "./location.ts";
import { parseWebRequest } from "./speech.ts";

const ORIGIN = "https://icatch.ai.kr";
const isInternal = (url: string) => isInternalUrlOf(url, ORIGIN);

test("우리 페이지가 보낸 위치 부탁 넷만 받는다", () => {
  for (const action of ["check", "ask", "read", "settings"]) {
    assert.deepEqual(
      parseWebRequest(JSON.stringify({ kind: "location", action }), ORIGIN, isInternal),
      { kind: "location", action },
    );
  }
  assert.equal(parseWebRequest('{"kind":"location","action":"start"}', ORIGIN, isInternal), null);
  assert.equal(parseWebRequest('{"kind":"location","action":"watch"}', ORIGIN, isInternal), null);
  assert.equal(
    parseWebRequest('{"kind":"location","action":"read"}', "https://evil.example", isInternal),
    null,
  );
});

test("🚨 좌표는 소수점 둘째 자리까지만 남는다", () => {
  assert.deepEqual(coarsePosition({ latitude: 35.233_456_7, longitude: 129.081_234_5 }), {
    lat: 35.23,
    lon: 129.08,
  });
  assert.deepEqual(coarsePosition({ latitude: -33.868_8, longitude: 151.209_3 }), {
    lat: -33.87,
    lon: 151.21,
  });
  for (const value of [35.123_456, 129.999_9, -0.004, 0.005_1]) {
    const digits = String(coarsen(value)).split(".")[1] ?? "";
    assert.ok(digits.length <= 2, `${value} → ${coarsen(value)}`);
  }
  assert.equal(Object.is(coarsen(-0.001), -0), false);
});

test("숫자가 아니거나 범위를 벗어난 좌표는 넘기지 않는다", () => {
  for (const coords of [
    { latitude: Number.NaN, longitude: 129 },
    { latitude: 35, longitude: Number.POSITIVE_INFINITY },
    { latitude: 91, longitude: 129 },
    { latitude: 35, longitude: -181 },
  ]) {
    assert.equal(coarsePosition(coords), null);
  }
});

test("권한 응답을 상태로 — 묻지 않은 확인의 허용 전은 거절이 아니다", () => {
  assert.equal(permissionStatus({ granted: true, canAskAgain: true }, false), "granted");
  assert.equal(permissionStatus({ granted: true, canAskAgain: false }, true), "granted");
  assert.equal(permissionStatus({ granted: false, canAskAgain: true }, false), "prompt");
  assert.equal(permissionStatus({ granted: false, canAskAgain: true }, true), "declined");
  assert.equal(permissionStatus({ granted: false, canAskAgain: false }, false), "denied");
  assert.equal(permissionStatus({ granted: false, canAskAgain: false }, true), "denied");
});

test("창구 스크립트가 종류를 붙여 보내고, 이미 있으면 덮지 않는다", () => {
  const sent: string[] = [];
  const window: Record<string, unknown> = {
    ReactNativeWebView: { postMessage: (message: string) => sent.push(message) },
  };
  runInNewContext(locationBridgeScript(true), { window });
  const bridge = (
    window.icatch as {
      location: {
        available: boolean;
        check(): void;
        ask(): void;
        read(): void;
        openSettings(): void;
      };
    }
  ).location;
  assert.equal(bridge.available, true);
  bridge.check();
  bridge.ask();
  bridge.read();
  bridge.openSettings();
  assert.deepEqual(
    sent.map((message) => parseWebRequest(message, ORIGIN, isInternal)),
    [
      { kind: "location", action: "check" },
      { kind: "location", action: "ask" },
      { kind: "location", action: "read" },
      { kind: "location", action: "settings" },
    ],
  );

  runInNewContext(locationBridgeScript(false), { window });
  assert.equal(
    (window.icatch as { location: { available: boolean } }).location.available,
    true,
  );
});

test("셸 → 웹 이벤트는 값을 JSON 으로만 싣는다", () => {
  const received: unknown[] = [];
  class CustomEvent {
    type: string;
    init: { detail: unknown };
    constructor(type: string, init: { detail: unknown }) {
      this.type = type;
      this.init = init;
    }
  }
  const window = {
    dispatchEvent: (event: CustomEvent) => received.push([event.type, event.init.detail]),
  };
  runInNewContext(locationEventScript({ type: "position", lat: 35.23, lon: 129.08 }), {
    window,
    CustomEvent,
  });
  runInNewContext(locationEventScript({ type: "permission", action: "ask", status: "declined" }), {
    window,
    CustomEvent,
  });
  runInNewContext(locationEventScript({ type: "unavailable", reason: "not-granted" }), {
    window,
    CustomEvent,
  });
  // vm 안에서 만든 객체라 프로토타입이 다르다 — 값만 비교한다.
  assert.deepEqual(JSON.parse(JSON.stringify(received)), [
    ["icatch:location", { type: "position", lat: 35.23, lon: 129.08 }],
    ["icatch:location", { type: "permission", action: "ask", status: "declined" }],
    ["icatch:location", { type: "unavailable", reason: "not-granted" }],
  ]);
});
