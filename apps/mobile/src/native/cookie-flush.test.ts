/**
 * 백그라운드로 갈 때 웹뷰 쿠키 flush (#469).
 *
 * 🚨 실제로 디스크에 쓰이는지는 여기서 못 본다. 개발 빌드에서 logcat 으로, #468 배포 뒤 실기기에서
 *    "켜고 바로 끄기 → 다시 켜기" 로 본다.
 *
 * 실행: `pnpm test`
 */

import assert from "node:assert/strict";
import test from "node:test";

import { flushOnBackground } from "./cookie-flush.ts";

test("백그라운드로 갈 때만 flush 한다", () => {
  let calls = 0;
  const flush = async () => {
    calls += 1;
  };

  flushOnBackground("active", flush);
  flushOnBackground("inactive", flush);
  assert.equal(calls, 0);

  flushOnBackground("background", flush);
  assert.equal(calls, 1);
});

test("모듈이 없는 환경(iOS · Expo Go)에서는 아무것도 하지 않는다", () => {
  assert.doesNotThrow(() => flushOnBackground("background", undefined));
});

test("🚨 flush 가 실패해도 던지지 않는다 — 셸이 죽으면 안 된다", async () => {
  let rejected = false;
  const flush = () => {
    rejected = true;
    return Promise.reject(new Error("io"));
  };

  flushOnBackground("background", flush);
  // 처리되지 않은 거절이 있으면 Node 러너가 테스트를 실패로 만든다.
  await new Promise((resolve) => setImmediate(resolve));
  assert.ok(rejected);
});
