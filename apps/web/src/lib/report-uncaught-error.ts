import { ApiError, NetworkError } from "@/lib/api/errors";
import {
  browserEnvironment,
  buildClientErrorReport,
  sendClientError,
  type ClientEnvironment,
  type ClientErrorReport,
} from "@/lib/report-render-error";

/**
 * 렌더 밖에서 난 예외도 서버로 보낸다 (#732) — `window` 의 `error` · `unhandledrejection`.
 *
 * 에러 바운더리는 렌더 도중 던져진 예외만 잡는다 (`lib/report-render-error.ts`). 클릭 핸들러 안의
 * 예외, `setTimeout` · async 함수의 예외, 아무도 받지 않은 Promise rejection 은 바운더리에 닿지 않고
 * 보호자 폰의 콘솔에만 남았다. 그것을 같은 창구(`POST /client-errors` → Discord `browser-alert`)로 보낸다.
 * 보내는 모양도 렌더 오류와 같다 — 이름 · digest · 화면 경로 · 기기 요약, 메시지는 없다.
 *
 * 바운더리가 잡은 렌더 오류는 여기로 다시 오지 않는다 — Next 의 `onCaughtError` 는 콘솔에만 찍고
 * `window` 로 다시 던지지 않는다 (next/dist/client/react-client-callbacks/error-boundary-callbacks.js).
 * 그래서 한 오류가 두 번 보고되지 않는다.
 *
 * 🚨 잡음은 보내지 않는다 (`isNoise`). 알림 채널이 잡음으로 차면 정작 봐야 할 알림이 묻힌다.
 * 🚨 같은 오류(이름 + 경로)는 페이지를 연 동안 한 번만, 전체도 `LIMIT` 건까지. 렌더 오류는 화면이
 *    오류 화면으로 바뀌면서 멈추지만, 핸들러 예외는 누를 때마다 · 타이머가 돌 때마다 또 난다.
 *    상한은 이 함수의 클로저 상태다 — 모듈 상태로 두면 테스트끼리 섞인다.
 * 🚨 기본 동작(`preventDefault`)을 막지 않는다 — 브라우저가 콘솔에 찍는 것은 그대로 둔다. 그 줄의
 *    메시지는 우리가 못 막는다 (렌더 오류의 React 콘솔 출력과 같다, docs/web/api-client-v1.md §4).
 */

/** 페이지를 연 동안 보내는 최대 건수. 서로 다른 오류가 계속 나도 채널을 도배하지 않게. */
const LIMIT = 5;

/** 듣는 대상. 테스트는 `EventTarget` 을 직접 넘기고, 화면은 `window` 다. */
type Listenable = Pick<EventTarget, "addEventListener" | "removeEventListener">;

export interface UncaughtErrorOptions {
  target?: Listenable;
  /** 우리 스크립트의 출처 — 다른 출처(브라우저 확장)의 예외를 거른다. 기본은 `window.location.origin`. */
  origin?: string;
  environment?: () => ClientEnvironment | null;
  send?: (report: ClientErrorReport) => Promise<void>;
  limit?: number;
}

/**
 * 보내지 않을 예외인가. 메시지는 밖으로 안 내보내고, 여기서도 들여다보지 않는다 — 종류만 본다.
 *
 * - `ApiError` — api 가 자기 5xx 를 이미 알린다(`api-alert`). 4xx 는 업무 응답이다
 * - `NetworkError` — 보호자 쪽 연결이 끊긴 것. 서버가 죽은 것은 컨테이너 감시가 알린다
 * - `AbortError` — 화면을 떠나며 요청을 취소한 것
 */
export function isNoise(error: unknown): boolean {
  if (error instanceof ApiError || error instanceof NetworkError) return true;
  const name = (error as { name?: unknown } | null | undefined)?.name;
  return name === "AbortError";
}

/**
 * `error` 이벤트에서 보낼 예외를 꺼낸다. 보내지 않을 것이면 `undefined`.
 *
 * - 예외 객체가 없다(`null`) — 다른 출처 스크립트의 "Script error." 와 ResizeObserver 의 반복 알림이
 *   이 모양이다. 꺼낼 것이 없다. (`throw "문자열"` 은 `null` 이 아니라 그 문자열이라 보낸다)
 * - 우리 출처가 아닌 파일에서 났다 — 브라우저 확장이 페이지에 넣은 스크립트다
 */
function errorFromErrorEvent(event: Event, origin: string): unknown {
  const { error, filename } = event as Event & { error?: unknown; filename?: unknown };
  if (error === null || error === undefined) return undefined;
  if (typeof filename === "string" && filename !== "" && !filename.startsWith(`${origin}/`)) {
    return undefined;
  }
  return error;
}

/** 듣기 시작하고, 그만 듣는 함수를 돌려준다. `window` 가 없으면(서버) 아무것도 안 한다. */
export function installUncaughtErrorReporting(options: UncaughtErrorOptions = {}): () => void {
  const target = options.target ?? (typeof window === "undefined" ? null : window);
  if (!target) return () => {};
  const origin = options.origin ?? (typeof window === "undefined" ? "" : window.location.origin);
  const environment = options.environment ?? browserEnvironment;
  const send = options.send ?? sendClientError;
  const limit = options.limit ?? LIMIT;
  const seen = new Set<string>();

  function report(error: unknown): void {
    if (seen.size >= limit || isNoise(error)) return;
    const env = environment();
    if (!env) return;
    const body = buildClientErrorReport(error, env);
    const key = `${body.name} ${body.path}`;
    if (seen.has(key)) return;
    seen.add(key);
    void send(body);
  }

  const onError = (event: Event) => {
    const error = errorFromErrorEvent(event, origin);
    if (error !== undefined) report(error);
  };
  const onRejection = (event: Event) => {
    report((event as Event & { reason?: unknown }).reason);
  };

  target.addEventListener("error", onError);
  target.addEventListener("unhandledrejection", onRejection);
  return () => {
    target.removeEventListener("error", onError);
    target.removeEventListener("unhandledrejection", onRejection);
  };
}
