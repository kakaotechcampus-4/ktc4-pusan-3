/**
 * 앱이 백그라운드로 갈 때 웹뷰 쿠키를 디스크에 쓴다 (#469).
 *
 * 왜 필요한가 — 웹은 앱을 켤 때 refresh 쿠키로 로그인을 되살리고(#465), 그때마다 서버가 쿠키를
 * 회전시킨다(#468). 웹뷰는 쿠키를 주기적으로만 디스크에 기록하고(Chromium 기준 약 30초),
 * react-native-webview 는 페이지 로드가 끝날 때만 flush 하는데 회전은 그 뒤다. 그 사이에 앱을
 * 끄면 디스크에는 직전 refresh 가 남는다. 다음 실행에서 그것을 보내면 서버는 30초 유예 밖의
 * 재사용으로 보고 **세션을 지운다** — "켜서 잠깐 보고 끄기" 만으로 로그아웃된다.
 *
 * Android 는 최근 앱 화면으로 가도 `background` 다 (액티비티 `onPause`). 스와이프로 끄기 전에
 * 반드시 지나간다.
 *
 * 🚨 셸은 쿠키 값을 모른다. 기록 시점만 앞당긴다 (CLAUDE.md §1 "토큰을 들고 있지 않다").
 */

/** React Native `AppState` 의 값. 이 파일은 Node 테스트에서도 읽혀서 RN 타입을 가져오지 않는다. */
export type AppStateValue = "active" | "background" | "inactive" | "unknown" | "extension";

/**
 * 상태가 바뀔 때 부른다. 백그라운드로 갈 때만 flush 한다.
 *
 * 🚨 실패해도 던지지 않는다. flush 는 기록을 앞당기는 것뿐이라, 못 하면 웹뷰의 주기 기록을
 *    기다릴 뿐이다 — 이것 때문에 셸이 죽으면 안 된다.
 */
export function flushOnBackground(
  state: AppStateValue,
  flush: (() => Promise<void>) | undefined,
): void {
  if (state !== "background" || !flush) return;
  flush().catch(() => {});
}
