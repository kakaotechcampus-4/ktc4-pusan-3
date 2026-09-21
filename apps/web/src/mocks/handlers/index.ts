import { authHandlers } from "./auth";
import { calendarHandlers } from "./calendar";
import { childrenHandlers } from "./children";
import { memoryHandlers } from "./memories";
import { photoHandlers } from "./photos";
import { profileHandlers } from "./profile";
import { runHandlers } from "./runs";
import { settingsHandlers } from "./settings";
import { suggestionHandlers } from "./suggestions";

/**
 * 화면 01~11 이 쓰는 엔드포인트. 이제 빠진 화면이 없다.
 * 여기 없는 경로는 onUnhandledRequest 가 콘솔에 경고로 알려준다.
 *
 * 🚨 **순서가 뜻을 갖는 자리가 하나 있다.** `suggestionHandlers` 의
 *    `POST /children/{cid}/suggestions`(05)와 `memoryHandlers` 의
 *    `GET /children/{cid}/suggestions`(07)는 **같은 경로 다른 메서드**다 —
 *    msw 는 메서드로 먼저 가르므로 충돌하지 않지만, 한쪽을 지울 때 다른 쪽이 같이
 *    사라지지 않게 파일을 나눠 뒀다.
 */
export const handlers = [
  ...authHandlers,
  ...childrenHandlers,
  // 🚨 `childrenHandlers` **뒤**여야 한다. msw 는 먼저 등록된 핸들러가 이기는데,
  //    `GET /children/:cid` 는 `GET /children/:cid/home` 보다 넓은 패턴이 아니므로
  //    충돌하지는 않는다 — 다만 순서가 뜻을 갖는 자리(아래 주석)와 같은 파일이라 붙여 둔다.
  ...profileHandlers,
  ...runHandlers,
  // 🚨 `GET /runs/{rid}/events` 는 `runHandlers` 한 곳에만 있다 — 사진 run 은 그 안에서 갈린다
  //    (`handlers/photos.ts` 의 주석). 여기에 같은 경로를 또 등록하지 않는다.
  ...photoHandlers,
  ...suggestionHandlers,
  ...memoryHandlers,
  ...calendarHandlers,
  ...settingsHandlers,
];
