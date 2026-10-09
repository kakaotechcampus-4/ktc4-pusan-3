import {
  buildWebAlert,
  createWebAlerter,
  shouldSendWebAlert,
  type ErrorRequest,
  type RequestErrorContext,
  type WebAlert,
} from "@/lib/server-error-alert";

/**
 * Next 서버의 계측 진입점. `register` 는 서버가 뜰 때 한 번, `onRequestError` 는 서버에서 요청을
 * 처리하다 예외가 날 때마다 불린다 (Next 15+ 안정 기능, 설정 없이 켜진다).
 *
 * 하는 일은 하나 — 서버 쪽 오류를 Discord 로 (`lib/server-error-alert.ts`). 웹훅 URL 은 서버
 * 환경변수 `ALERT_WEBHOOK_URL` (compose 가 넘긴다 · 브라우저 번들에 안 들어간다). 개발 모드에서는
 * 보내지 않는다.
 */

let send: ((alert: WebAlert) => Promise<void>) | null = null;

export function register(): void {
  const url = process.env.ALERT_WEBHOOK_URL;
  send = shouldSendWebAlert({ nodeEnv: process.env.NODE_ENV, url })
    ? createWebAlerter({ url: url! })
    : null;
}

export async function onRequestError(
  error: unknown,
  request: ErrorRequest,
  context: RequestErrorContext,
): Promise<void> {
  if (!send) return;
  await send(buildWebAlert(error, request, context, { env: process.env.ALERT_ENV ?? "prod" }));
}
