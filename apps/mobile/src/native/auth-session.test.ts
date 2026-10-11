/**
 * 로그인 왕복의 **셸 몫** 점검 — 어떤 이동을 인증 세션으로 열고, 돌아온 주소를 어디로 싣는가.
 *
 * 🚨 여기서 보는 것은 판정과 주소 조립뿐이다. Custom Tabs 가 실제로 열리고 닫히는지 · 복귀가
 *    `success` 로 오는지는 기기에서만 보인다 (#210 검증 방법).
 *
 * 실행: `pnpm test`
 */

import assert from "node:assert/strict";
import test from "node:test";
import vm from "node:vm";

import {
  AUTH_RETURN_URL,
  callbackUrlFromReturn,
  isAuthStartUrl,
  navigateScript,
} from "./auth-session.ts";

const WEB = "http://localhost:3000";

test("서버가 주는 시작 URL 은 인증 세션으로 연다", () => {
  // 웹이 붙이는 쿼리까지 실린 모양 (apps/web/src/lib/auth/oauth.ts startOAuthLogin)
  assert.ok(isAuthStartUrl("http://localhost:8000/api/v1/auth/kakao?client=app&bind=abc"));
  assert.ok(isAuthStartUrl("https://api.example.com/api/v1/auth/kakao"));
  assert.ok(isAuthStartUrl("https://api.example.com/api/v1/auth/kakao/"));
});

test("시작이 아닌 auth 경로 · 다른 외부 링크는 인증 세션으로 열지 않는다", () => {
  // 카카오가 돌려보내는 자리 — 여기 걸리면 세션 안에서 세션이 또 열린다
  assert.ok(!isAuthStartUrl("https://api.example.com/api/v1/auth/kakao/callback?code=x"));
  assert.ok(!isAuthStartUrl("https://api.example.com/api/v1/auth/kakao/status"));
  assert.ok(!isAuthStartUrl("https://api.example.com/api/v1/policies/service_terms/draft-1"));
  assert.ok(!isAuthStartUrl("https://kauth.kakao.com/oauth/authorize?client_id=x"));
  assert.ok(!isAuthStartUrl("https://example.com/docs/api/v1/auth/kakao"));
});

test("http(s) 가 아닌 것 · 깨진 주소는 시작 URL 이 아니다", () => {
  assert.ok(!isAuthStartUrl("icatch://auth?code=x"));
  assert.ok(!isAuthStartUrl("intent://api/v1/auth/kakao#Intent;scheme=https;end"));
  assert.ok(!isAuthStartUrl("not a url"));
  assert.ok(!isAuthStartUrl(""));
});

test("돌아온 쿼리를 손대지 않고 웹의 복귀 화면에 붙인다", () => {
  assert.equal(
    callbackUrlFromReturn(`${AUTH_RETURN_URL}?code=a-b_c`, WEB),
    `${WEB}/auth/callback?code=a-b_c`,
  );
  // 실패도 같은 길로 간다 — 문구는 웹이 코드를 보고 만든다
  assert.equal(
    callbackUrlFromReturn(`${AUTH_RETURN_URL}?error=invalid_bind`, WEB),
    `${WEB}/auth/callback?error=invalid_bind`,
  );
  // 쿼리가 없으면 웹이 조용히 00 으로 되돌린다
  assert.equal(callbackUrlFromReturn(AUTH_RETURN_URL, WEB), `${WEB}/auth/callback`);
});

test("우리 복귀 주소가 아니면 싣지 않는다", () => {
  assert.equal(callbackUrlFromReturn("icatch://authXXX?code=x", WEB), null);
  assert.equal(callbackUrlFromReturn("icatch://auth/evil?code=x", WEB), null);
  assert.equal(callbackUrlFromReturn("icatch://expo-development-client/?url=x", WEB), null);
  assert.equal(callbackUrlFromReturn("https://evil.example/auth?code=x", WEB), null);
});

test("이동 스크립트는 받은 주소 그대로 replace 하고, 따옴표로 깨지지 않는다", () => {
  const moved: string[] = [];
  const sandbox = { window: { location: { replace: (url: string) => moved.push(url) } } };
  vm.createContext(sandbox);

  const tricky = `${WEB}/auth/callback?error=x");alert(1);//`;
  const result = vm.runInContext(navigateScript(tricky), sandbox);

  assert.deepEqual(moved, [tricky]);
  // 다른 주입 스크립트와 같이 `true;` 로 닫는다 (react-native-webview 권고 — 빠지면 조용히 실패할 때가 있다)
  assert.equal(result, true);
});
