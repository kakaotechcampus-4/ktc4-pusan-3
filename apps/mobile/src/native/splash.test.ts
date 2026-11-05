/**
 * 스플래시 — 웹 부탁 검사 · 웹에 넣는 창구 (#690).
 *
 * 🚨 스플래시가 실제로 내려가는 시점은 여기서 못 본다. Android 빌드에서 본다 (`apps/mobile/CLAUDE.md` "스플래시").
 *
 * 실행: `pnpm test`
 */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";
import test from "node:test";

import { isInternalUrlOf } from "./internal-url.ts";
import { SPLASH_AFTER_LOAD_MS, SPLASH_MAX_MS, splashBridgeScript } from "./splash.ts";
import { parseWebRequest } from "./speech.ts";

const ORIGIN = "https://icatch.ai.kr";
const isInternal = (url: string) => isInternalUrlOf(url, ORIGIN);

test("우리 페이지가 보낸 첫 화면 알림만 받는다", () => {
  assert.deepEqual(parseWebRequest('{"kind":"splash","action":"done"}', ORIGIN, isInternal), {
    kind: "splash",
    action: "done",
  });
  assert.equal(parseWebRequest('{"kind":"splash","action":"show"}', ORIGIN, isInternal), null);
  assert.equal(
    parseWebRequest('{"kind":"splash","action":"done"}', "https://evil.example", isInternal),
    null,
  );
  // 종류가 섞이지 않는다 — 말로 적기의 "그만" 으로 읽히면 안 된다.
  assert.equal(parseWebRequest('{"kind":"speech","action":"done"}', ORIGIN, isInternal), null);
});

test("창구는 첫 화면 알림을 셸에 보내고, 이미 있으면 덮지 않는다", () => {
  const sent: string[] = [];
  const window: Record<string, any> = {
    ReactNativeWebView: { postMessage: (m: string) => sent.push(m) },
  };
  runInNewContext(splashBridgeScript(), { window, JSON });
  window.icatch.splash.done();
  assert.deepEqual(sent.map((m) => JSON.parse(m)), [{ kind: "splash", action: "done" }]);

  const before = window.icatch.splash;
  runInNewContext(splashBridgeScript(), { window, JSON });
  assert.equal(window.icatch.splash, before);
});

test("셸 밖(브라우저)에서는 아무것도 보내지 않고 던지지 않는다", () => {
  const window: Record<string, any> = {};
  runInNewContext(splashBridgeScript(), { window, JSON });
  assert.doesNotThrow(() => window.icatch.splash.done());
});

test("🚨 문서 뒤의 기다림이 전체 상한보다 짧다 — 아니면 문서 뒤 기다림이 뜻이 없다", () => {
  assert.ok(SPLASH_AFTER_LOAD_MS < SPLASH_MAX_MS);
});

test("🚨 스플래시와 셸 덮개의 바탕색 · 그림 크기가 같다 — 다르면 내려갈 때 색이나 그림이 튄다", () => {
  const app = JSON.parse(readFileSync(new URL("../../app.json", import.meta.url), "utf8"));
  const plugin = app.expo.plugins.find(
    (entry: unknown) => Array.isArray(entry) && entry[0] === "expo-splash-screen",
  );
  const shell = readFileSync(new URL("../../App.tsx", import.meta.url), "utf8");
  const canvas = shell.match(/const CANVAS = "(#[0-9a-f]{6})"/i)?.[1];
  const web = readFileSync(new URL("../../../web/src/app/globals.css", import.meta.url), "utf8");
  const webCanvas = web.match(/--color-canvas:\s*(#[0-9a-f]{6})/i)?.[1];
  assert.ok(canvas && webCanvas);
  assert.equal(canvas.toLowerCase(), webCanvas.toLowerCase());
  assert.equal(plugin?.[1]?.backgroundColor?.toLowerCase(), canvas.toLowerCase());
  // Android 12+ 는 192dp 원으로 잘라 그린다. 그림은 그 원에 맞춰 만들었다 (`scripts/build-splash.py`).
  assert.equal(plugin?.[1]?.imageWidth, 192);
  // 셸의 덮개는 같은 그림을 같은 크기로 둔다 — 다르면 네이티브 스플래시가 내려갈 때 그림이 튄다.
  assert.equal(shell.match(/const SPLASH_IMAGE_DP = (\d+);/)?.[1], "192");
});
