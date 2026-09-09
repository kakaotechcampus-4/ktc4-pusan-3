import { authHandlers } from "./auth";
import { childrenHandlers } from "./children";
import { runHandlers } from "./runs";
import { suggestionHandlers } from "./suggestions";

/**
 * 화면 01~06 이 쓰는 엔드포인트만 있다. 07~10 은 다음 이슈다.
 * 여기 없는 경로는 onUnhandledRequest 가 콘솔에 경고로 알려준다.
 */
export const handlers = [
  ...authHandlers,
  ...childrenHandlers,
  ...runHandlers,
  ...suggestionHandlers,
];
