/**
 * Android 물리 뒤로가기 — 웹뷰 히스토리로 갈지, 앱을 닫을지 (#462).
 *
 * 🚨 토스트와 실제로 앱이 닫히는지는 여기서 못 본다. 기기에서 하단 네비 네 칸과 설정 화면을 오가며 본다.
 *
 * 실행: `pnpm test`
 */

import assert from "node:assert/strict";
import test from "node:test";

import { EXIT_WINDOW_MS, decideBack, isRootUrlOf } from "./back-press.ts";

const ORIGIN = "https://icatch.example";

test("하단 네비 네 칸은 상위 화면이다", () => {
  for (const tab of ["home", "calendar", "memories", "profile"]) {
    assert.ok(isRootUrlOf(`${ORIGIN}/child/abc/${tab}`, ORIGIN), tab);
  }
  // 끝 슬래시 · 쿼리 · 해시는 보지 않는다
  assert.ok(isRootUrlOf(`${ORIGIN}/child/abc/home/`, ORIGIN));
  assert.ok(isRootUrlOf(`${ORIGIN}/child/abc/calendar?date=2026-10-15`, ORIGIN));
  assert.ok(isRootUrlOf(`${ORIGIN}/child/abc/memories#top`, ORIGIN));
});

test("하위 화면과 아이 밖 화면은 상위가 아니다", () => {
  assert.ok(!isRootUrlOf(`${ORIGIN}/child/abc/settings`, ORIGIN));
  assert.ok(!isRootUrlOf(`${ORIGIN}/child/abc/settings/help`, ORIGIN));
  assert.ok(!isRootUrlOf(`${ORIGIN}/child/abc/profile/growth`, ORIGIN));
  assert.ok(!isRootUrlOf(`${ORIGIN}/`, ORIGIN));
  assert.ok(!isRootUrlOf(`${ORIGIN}/start`, ORIGIN));
  assert.ok(!isRootUrlOf(`${ORIGIN}/child/home`, ORIGIN));
});

test("다른 출처와 깨진 주소는 상위가 아니다", () => {
  assert.ok(!isRootUrlOf("https://other.example/child/abc/home", ORIGIN));
  assert.ok(!isRootUrlOf("not a url", ORIGIN));
  assert.ok(!isRootUrlOf("", ORIGIN));
});

test("하위 화면은 히스토리로 한 칸 간다", () => {
  assert.equal(decideBack({ atRoot: false, canGoBack: true, now: 0, lastPressAt: null }), "go-back");
});

test("상위 화면은 히스토리가 있어도 뒤로 가지 않고 먼저 묻는다", () => {
  assert.equal(
    decideBack({ atRoot: true, canGoBack: true, now: 10_000, lastPressAt: null }),
    "confirm-exit",
  );
});

test("더 갈 히스토리가 없으면 상위가 아니어도 먼저 묻는다", () => {
  assert.equal(
    decideBack({ atRoot: false, canGoBack: false, now: 10_000, lastPressAt: null }),
    "confirm-exit",
  );
});

test("안내 뒤 시간 안에 또 누르면 닫고, 지나면 다시 묻는다", () => {
  const first = 10_000;
  assert.equal(
    decideBack({ atRoot: true, canGoBack: true, now: first + EXIT_WINDOW_MS - 1, lastPressAt: first }),
    "exit",
  );
  assert.equal(
    decideBack({ atRoot: true, canGoBack: true, now: first + EXIT_WINDOW_MS, lastPressAt: first }),
    "confirm-exit",
  );
});

test("안내 직후라도 하위 화면에서는 히스토리로 간다", () => {
  assert.equal(
    decideBack({ atRoot: false, canGoBack: true, now: 10_100, lastPressAt: 10_000 }),
    "go-back",
  );
});
