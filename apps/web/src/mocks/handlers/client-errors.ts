import { http, HttpResponse } from "msw";

import { url } from "./helpers";

/**
 * 화면 오류 보고 — `POST /client-errors` (멘토 #267 2번 · #166). 서버는 받아서 ERROR 로그로만
 * 남기고 204 를 돌려준다. 목도 같다 — 내용을 들여다보지 않는다.
 * 보내는 쪽은 `lib/report-render-error.ts`, 모양 검사는 서버의 Pydantic 이 한다.
 */
export const clientErrorHandlers = [
  http.post(url("/client-errors"), async () => new HttpResponse(null, { status: 204 })),
];
