/**
 * 일정 알림 판정 — 웹 부탁 검사 · 알림 data → 화면 주소 · 권한 창 · 웹에 넣는 스크립트 (#609 FE-4).
 *
 * 🚨 실제로 FCM 토큰을 받고 알림을 누르는 것은 여기서 못 본다. Android 개발 빌드에서 본다.
 *
 * 실행: `pnpm test`
 */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";
import test from "node:test";

import { isInternalUrlOf } from "./internal-url.ts";
import {
  PUSH_CHANNEL_ID,
  pushBridgeScript,
  pushEventScript,
  pushOpenPath,
  responseData,
  shouldAskPermission,
} from "./push.ts";
import { parseWebRequest } from "./speech.ts";

const ORIGIN = "https://icatch.ai.kr";
const isInternal = (url: string) => isInternalUrlOf(url, ORIGIN);
const CHILD = "7c1d0c2e-5b4a-4f1e-9d3a-0a1b2c3d4e5f";

test("우리 페이지가 보낸 알림 부탁 셋만 받는다", () => {
  for (const action of ["ready", "sync", "ask"]) {
    assert.deepEqual(
      parseWebRequest(JSON.stringify({ kind: "push", action }), ORIGIN, isInternal),
      { kind: "push", action },
    );
  }
  assert.equal(parseWebRequest('{"kind":"push","action":"start"}', ORIGIN, isInternal), null);
  assert.equal(parseWebRequest('{"kind":"push","action":"ask"}', "https://evil.example", isInternal), null);
});

// ⚠️ 이 판정은 모양만 본다. 알림 부탁이 마이크를 끄지 않는 것은 `App.tsx` 의 `onMessage` 가 종류부터 가르기 때문이다.
test("알림 부탁과 말로 적기 부탁은 종류가 섞이지 않는다", () => {
  assert.deepEqual(parseWebRequest('{"kind":"push","action":"sync"}', ORIGIN, isInternal), {
    kind: "push",
    action: "sync",
  });
  assert.equal(parseWebRequest('{"kind":"speech","action":"sync"}', ORIGIN, isInternal), null);
});

test("서버 data 로 그 아이의 그날 09 를 연다", () => {
  assert.equal(
    pushOpenPath({ type: "schedule_reminder", child_id: CHILD, event_id: "e1", date: "2026-11-02" }),
    `/child/${CHILD}/calendar?date=2026-11-02`,
  );
});

test("🚨 data 의 모양이 다르면 주소를 만들지 않는다 — 앱만 연다", () => {
  for (const data of [
    null,
    "text",
    { type: "medication", child_id: CHILD, date: "2026-11-02" },
    { type: "schedule_reminder", child_id: "../../evil", date: "2026-11-02" },
    { type: "schedule_reminder", child_id: CHILD, date: "javascript:x" },
    { type: "schedule_reminder", child_id: 1, date: "2026-11-02" },
  ]) {
    assert.equal(pushOpenPath(data), null);
  }
});

test("앱 밖에서 FCM 이 띄운 알림은 data 를 원래 메시지에서 꺼낸다", () => {
  const data = { type: "schedule_reminder", child_id: CHILD, date: "2026-11-02" };
  const request = (content: object, trigger: unknown) => ({
    notification: { request: { content, trigger } },
  });

  assert.deepEqual(responseData(request({ data }, null)), data);
  assert.deepEqual(responseData(request({ data: {} }, { remoteMessage: { data } })), data);
  assert.equal(responseData(request({}, { type: "push" })), null);
});

test("🚨 앱 밖 알림의 기본 채널이 셸이 만드는 채널과 같다 — 다르면 FCM 이 영문 기본 채널을 만든다", () => {
  const app = JSON.parse(readFileSync(new URL("../../app.json", import.meta.url), "utf8"));
  const plugin = app.expo.plugins.find(
    (entry: unknown) => Array.isArray(entry) && entry[0] === "expo-notifications",
  );
  assert.equal(plugin?.[1]?.defaultChannel, PUSH_CHANNEL_ID);
});

test("🚨 권한 창은 허용 전이고 OS 가 다시 물을 수 있을 때 띄운다 — 안 물어도 denied 로 온다", () => {
  // Android 13+: 한 번도 묻지 않았어도 status 는 denied 다 (expo-notifications). canAskAgain 이 가른다.
  assert.equal(shouldAskPermission({ status: "denied", canAskAgain: true }), true);
  assert.equal(shouldAskPermission({ status: "undetermined", canAskAgain: true }), true);
  // 두 번 거절하면 OS 가 막는다.
  assert.equal(shouldAskPermission({ status: "denied", canAskAgain: false }), false);
  assert.equal(shouldAskPermission({ status: "granted", canAskAgain: true }), false);
});

/** 웹뷰에 넣는 스크립트를 가짜 window 에서 실제로 돌린다. */
function runInPage(script: string, window: Record<string, unknown>) {
  const events: Array<{ type: string; detail: unknown }> = [];
  // 🚨 Node 의 타입 스트리핑은 생성자 매개변수 속성(`public type`)을 못 읽는다 — 칸을 따로 적는다.
  class CustomEvent {
    type: string;
    detail: unknown;
    constructor(type: string, init: { detail: unknown }) {
      this.type = type;
      this.detail = init.detail;
    }
  }
  window.dispatchEvent = (event: { type: string; detail: unknown }) => events.push(event);
  runInNewContext(script, { window, CustomEvent, JSON });
  return events;
}

test("창구는 부탁을 셸에 보내고, 이미 있으면 덮지 않는다", () => {
  const sent: string[] = [];
  const window: Record<string, any> = { ReactNativeWebView: { postMessage: (m: string) => sent.push(m) } };
  runInPage(pushBridgeScript(true), window);
  window.icatch.push.ask();
  window.icatch.push.ready();

  assert.equal(window.icatch.push.available, true);
  assert.deepEqual(sent.map((m) => JSON.parse(m)), [
    { kind: "push", action: "ask" },
    { kind: "push", action: "ready" },
  ]);

  const first = window.icatch.push;
  runInPage(pushBridgeScript(false), window);
  assert.equal(window.icatch.push, first);
});

test("🚨 토큰에 따옴표가 섞여도 스크립트가 깨지지 않고 그대로 넘어간다", () => {
  const token = `a"b'c</script>\\d`;
  const events = runInPage(pushEventScript({ type: "token", token }), {});

  // 스크립트가 만든 값은 다른 실행 환경(vm)의 객체라 JSON 으로 옮겨 비교한다.
  assert.deepEqual(JSON.parse(JSON.stringify(events.map(({ type, detail }) => ({ type, detail })))), [
    { type: "icatch:push", detail: { type: "token", token } },
  ]);
});
