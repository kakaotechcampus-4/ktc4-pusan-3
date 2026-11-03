/**
 * 스플래시를 웹의 첫 화면까지 들고 있기 (#690).
 *
 * 웹 문서가 뜬 것(`onLoadEnd`)은 첫 화면이 선 것이 아니다. 로그인한 보호자는 그 뒤 세션을 되살리는 동안
 * 웹의 "불러오는 중…" 을 보고, 홈으로 옮겨 가서야 화면이 선다. 스플래시를 문서에서 내리면 켤 때 로딩 모양이
 * 스플래시 → "불러오는 중…" → 홈으로 한 번 더 바뀐다. 그래서 **웹이 첫 화면을 세우면 알려 주고**, 셸은
 * 그때 내린다.
 *
 * 🚨 "내린다" 는 네이티브 스플래시가 아니라 **셸의 덮개**(`App.tsx` 의 `SplashCover`)다. 네이티브 스플래시는 셸의
 *    첫 그림에서 바로 내려가고, 그 첫 그림이 같은 그림의 덮개다. 네이티브 스플래시를 들고 있으면 Android 가 앱을
 *    그리지 않아 웹의 화면 전환이 멈춘다 (`SplashCover` 머리말).
 *
 * 웹 쪽 짝은 `apps/web/src/lib/native/splash.ts` 다 (공통 `Screen` 이 처음 서면 `done`).
 * 이름(`window.icatch.splash` · 메시지 모양)을 바꾸려면 **양쪽을 같이** 고친다.
 *
 * 🚨 **웹이 끝내 알리지 않아도 스플래시에 갇히지 않는다.** 이 창구를 모르는 웹(배포 전 · 브라우저용 화면),
 *    알리기 전에 죽은 화면이 있다. 셸은 문서가 뜬 뒤 `SPLASH_AFTER_LOAD_MS`, 앱을 켠 뒤 `SPLASH_MAX_MS` 가
 *    지나면 내리고, 연결 실패로 가는 길에서는 바로 내린다 (`App.tsx`).
 */

/** 웹의 부탁. 첫 화면이 섰다 — 하나뿐이다. */
export type SplashAction = "done";

/**
 * 웹 문서가 뜬 뒤 웹의 `done` 을 이만큼 기다린다. 세션 확인(refresh → `/me`)이 끝나고 홈이 서는 데 걸리는 시간이다.
 * 넘으면 내리고 웹의 "불러오는 중…" 이 받는다 — 멈춘 그림보다 도는 스피너가 "기다리는 중" 을 말한다.
 */
export const SPLASH_AFTER_LOAD_MS = 3000;

/** 앱을 켠 뒤 이 시간이 지나면 무엇이 오든 내린다. 문서조차 오지 않는 느린 망이다. 그 뒤는 셸의 연결 대기 스피너다. */
export const SPLASH_MAX_MS = 8000;

/** `window.icatch.splash` 를 꽂는 스크립트. 🚨 이미 있으면 덮지 않는다 — 다른 창구와 같다. */
export function splashBridgeScript(): string {
  return `(function () {
  window.icatch = window.icatch || {};
  if (window.icatch.splash) return;
  window.icatch.splash = {
    done: function () {
      if (!window.ReactNativeWebView) return;
      window.ReactNativeWebView.postMessage(JSON.stringify({ kind: "splash", action: "done" }));
    },
  };
})();
true;`;
}
