/**
 * 알림을 눌러 열어도 되는 주소 (#609). 그 아이의 그날 09 캘린더 아니면 첫 화면이다.
 *
 * 🚨 **같은 모양을 세 곳이 따로 만든다** — 브라우저 워커 `public/push-sw.js` 의 `targetPath`, 앱 셸
 *    `apps/mobile/src/native/push.ts` 의 `pushOpenPath`, 그리고 받는 쪽인 여기. 하나를 바꾸면 셋을 같이 고친다.
 *    워커와 이 파일이 맞는지는 `push-sw.test.ts` 가 건다.
 * 🚨 서버나 셸이 보낸 문자열을 그대로 `router.push` 에 넣지 않는다. 이 모양만 받는다.
 *
 * 다른 모듈을 import 하지 않는다 — 셸 창구(`lib/native/push.ts`)와 웹 푸시(`lib/push/web-push.ts`)가 같이 쓴다.
 */
const OPEN_PATH = /^\/$|^\/child\/[A-Za-z0-9_-]{1,64}\/calendar\?date=\d{4}-\d{2}-\d{2}$/;

export function isPushOpenPath(path: string): boolean {
  return OPEN_PATH.test(path);
}
