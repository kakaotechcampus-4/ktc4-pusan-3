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
 *
 * 🚨 기다리지 않고 띄워 보낸다(`void`). Next 16 은 라우트 핸들러 예외에서 이 함수를 **기다린 뒤** 500 을
 *    낸다 — Discord 가 느리면 사용자 응답이 같이 늦어진다. 기다려서 얻는 것은 없다 (보내기 실패는 어차피
 *    조용하다). 서버는 계속 떠 있는 Node 프로세스라 띄워 보낸 요청도 끝까지 간다.
 * 환경 이름은 늘 prod 다 — 알림은 production 빌드(`next start`)에서만 나가고, 그건 배포 서버뿐이다.
 */

let send: ((alert: WebAlert) => Promise<void>) | null = null;

export function register(): void {
  const url = process.env.ALERT_WEBHOOK_URL;
  send = shouldSendWebAlert({ nodeEnv: process.env.NODE_ENV, url })
    ? createWebAlerter({ url: url! })
    : null;
}

export function onRequestError(
  error: unknown,
  request: ErrorRequest,
  context: RequestErrorContext,
): void {
  if (!send) return;
  void send(buildWebAlert(error, request, context, { env: "prod" }));
}
