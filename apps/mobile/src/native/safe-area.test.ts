/**
 * 웹에 넘기는 safe area 값 — 키보드가 열려 있으면 아래는 0 이다 (#462).
 *
 * 🚨 키보드 위 빈 띠가 실제로 사라지는지는 여기서 못 본다. 기기에서 홈 입력창을 눌러 본다.
 *
 * 실행: `pnpm test`
 */

import assert from "node:assert/strict";
import test from "node:test";

import { safeAreaScript, shellInsets } from "./safe-area.ts";

const INSETS = { top: 24, right: 0, bottom: 48, left: 0 };

test("키보드가 닫혀 있으면 값을 그대로 넘긴다", () => {
  assert.equal(shellInsets(INSETS, false), INSETS);
});

test("키보드가 열려 있으면 아래만 0 이다", () => {
  assert.deepEqual(shellInsets(INSETS, true), { top: 24, right: 0, bottom: 0, left: 0 });
});

test("스크립트는 반올림한 px 로 넘긴다", () => {
  assert.equal(
    safeAreaScript({ top: 24.4, right: 0, bottom: 47.6, left: 0 }),
    'window.__icatchSetInsets("24px","48px");true;',
  );
});
