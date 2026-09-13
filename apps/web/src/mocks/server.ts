import { setupServer } from "msw/node";

import { handlers } from "./handlers";

/**
 * 테스트용 목 서버. 브라우저 워커(browser.ts)와 **같은 핸들러**를 쓴다.
 *
 * 그게 핵심이다 — 테스트가 통과한다는 건 화면이 개발 중에 보는 그 목이 계약대로
 * 행동한다는 뜻이어야 한다. 테스트 전용 핸들러를 따로 두면 확인한 적 없는 목으로
 * 화면을 만들게 된다.
 */
export const server = setupServer(...handlers);
