/**
 * 앱 밖 링크를 어디로 보낼지 — in-app 브라우저인가, OS(`Linking`)인가.
 *
 * 🚨 탭이 실제로 앱 위에 뜨고 X 로 돌아오는지는 기기에서만 보인다 (#212 검증 방법).
 *
 * 실행: `pnpm test`
 */

import assert from "node:assert/strict";
import test from "node:test";

import { isWebUrl } from "./in-app-browser.ts";

test("웹 주소는 in-app 브라우저로 연다", () => {
  // 동의 화면 "전문 보기" 가 여는 서버 정본 HTML
  assert.ok(isWebUrl("http://localhost:8000/api/v1/policies/service_terms/draft-1"));
  assert.ok(isWebUrl("https://api.example.com/api/v1/policies/privacy_account/draft-1"));
  assert.ok(isWebUrl("HTTPS://EXAMPLE.COM/"));
});

test("웹 주소가 아닌 것은 OS 에 맡긴다", () => {
  assert.ok(!isWebUrl("tel:01000000000"));
  assert.ok(!isWebUrl("mailto:help@example.com"));
  assert.ok(!isWebUrl("intent://scan/#Intent;scheme=zxing;end"));
  assert.ok(!isWebUrl("kakaotalk://inappbrowser"));
  assert.ok(!isWebUrl("icatch://auth?code=x"));
});

test("깨진 주소는 웹 주소가 아니다", () => {
  assert.ok(!isWebUrl("not a url"));
  assert.ok(!isWebUrl(""));
});
