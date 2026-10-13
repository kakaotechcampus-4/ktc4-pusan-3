/**
 * 웹뷰 안에 둘 주소인가 — 출처가 같고, API 경로가 아니어야 한다 (#273).
 *
 * 🚨 nginx 로 API 를 웹 주소 아래에 둔 상태는 아직 없다. 약관이 in-app 브라우저로, 로그인이 인증 세션으로
 *    실제로 가는지는 nginx 가 붙은 뒤 기기에서 본다 (#273 검증 방법).
 *
 * 실행: `pnpm test`
 */

import assert from "node:assert/strict";
import test from "node:test";

import { isAuthStartUrl } from "./auth-session.ts";
import { isInternalUrlOf } from "./internal-url.ts";

const ORIGIN = "https://icatch.example";

test("우리 웹의 화면은 웹뷰 안에 둔다", () => {
  assert.ok(isInternalUrlOf(`${ORIGIN}/`, ORIGIN));
  assert.ok(isInternalUrlOf(`${ORIGIN}/child/abc/settings`, ORIGIN));
  assert.ok(isInternalUrlOf(`${ORIGIN}/auth/callback?code=x`, ORIGIN));
});

test("같은 출처의 /api/… 는 웹뷰 밖이다", () => {
  // 동의 화면 "전문 보기" 가 여는 서버 정본 HTML
  assert.ok(!isInternalUrlOf(`${ORIGIN}/api/v1/policies/service_terms/draft-1`, ORIGIN));
  assert.ok(!isInternalUrlOf(`${ORIGIN}/api/v1/auth/kakao`, ORIGIN));
  assert.ok(!isInternalUrlOf(`${ORIGIN}/api`, ORIGIN));
  assert.ok(!isInternalUrlOf(`${ORIGIN}/api/`, ORIGIN));
  assert.ok(!isInternalUrlOf(`${ORIGIN}/api?x=1`, ORIGIN));
});

test("같은 출처의 로그인 시작은 인증 세션 분기까지 간다", () => {
  // `App.tsx` 의 판정 순서 — 웹뷰 안이 아니어야 `isAuthStartUrl` 을 묻는다
  const url = `${ORIGIN}/api/v1/auth/kakao`;
  assert.ok(!isInternalUrlOf(url, ORIGIN));
  assert.ok(isAuthStartUrl(url));
});

test("/api 로 시작만 하는 웹 경로는 웹뷰 안에 남는다", () => {
  assert.ok(isInternalUrlOf(`${ORIGIN}/apis`, ORIGIN));
  assert.ok(isInternalUrlOf(`${ORIGIN}/api-docs`, ORIGIN));
  // 경로 중간의 api 는 API 경로가 아니다
  assert.ok(isInternalUrlOf(`${ORIGIN}/child/api/home`, ORIGIN));
});

test("다른 출처는 경로와 무관하게 웹뷰 밖이다", () => {
  // nginx 이전 — API 가 따로 떠 있는 지금
  assert.ok(!isInternalUrlOf("http://localhost:8000/api/v1/policies/service_terms/draft-1", ORIGIN));
  assert.ok(!isInternalUrlOf("https://kauth.kakao.com/oauth/authorize", ORIGIN));
  // 포트 · 스킴이 다르면 다른 출처다
  assert.ok(!isInternalUrlOf("https://icatch.example:8443/", ORIGIN));
  assert.ok(!isInternalUrlOf("http://icatch.example/", ORIGIN));
});

test("깨진 주소와 웹이 아닌 스킴은 웹뷰 밖이다", () => {
  assert.ok(!isInternalUrlOf("not a url", ORIGIN));
  assert.ok(!isInternalUrlOf("", ORIGIN));
  assert.ok(!isInternalUrlOf("icatch://auth?code=x", ORIGIN));
  assert.ok(!isInternalUrlOf("tel:01000000000", ORIGIN));
});
