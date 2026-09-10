import { API_BASE_URL } from "@/lib/env";
import { ApiError, NetworkError, type ApiErrorBody } from "./errors";

/* ── 인증 토큰 ────────────────────────────────────────────────────────────
 * Authorization: Bearer <token> — 예외 없음. 인증 없는 엔드포인트는 없다 (NF-09).
 * 저장 위치는 세션 스토어가 정하고, 여기는 "지금 값" 만 들고 있는다.
 * (스토어를 직접 import 하면 서버 컴포넌트에서 client.ts 를 못 쓰게 된다.)
 */
let authToken: string | null = null;

export function setAuthToken(token: string | null): void {
  authToken = token;
}

/* ── 401 unauthenticated ──────────────────────────────────────────────────
 * 세션 만료를 한 곳에서 처리한다 — 화면마다 401 을 다루면 어딘가는 빠뜨린다.
 * 스토어를 여기서 import 하지 않는 이유는 위와 같다(서버 컴포넌트에서 못 쓰게 된다).
 * 그래서 Providers 가 핸들러를 꽂아 준다.
 *
 * 🚨 `invalid_handoff`(401) 는 여기 걸리지 않는다. 그건 로그인 교환이 실패한 것이라
 *    "세션이 끊겼다" 와 다른 상태고, 콜백 화면이 자기 문구로 처리한다.
 */
let onUnauthenticated: (() => void) | null = null;

export function setUnauthenticatedHandler(handler: (() => void) | null): void {
  onUnauthenticated = handler;
}

/* ── Idempotency-Key ──────────────────────────────────────────────────────
 * 중복 실행이 기억을 두 번 쌓거나 캘린더에 두 번 쓰는 엔드포인트에만 필수다.
 * 헤더가 없으면 서버가 400 을 준다.
 */
const IDEMPOTENCY_REQUIRED: RegExp[] = [
  /^\/children\/[^/]+\/inputs$/,
  /^\/children\/[^/]+\/onboarding$/,
  /^\/children\/[^/]+\/photos$/,
  /^\/children\/[^/]+\/health-safety$/, // 승인 게이트 ㉡
  /^\/events\/[^/]+\/confirm$/, // 승인 게이트 ㉠
];

export function newIdempotencyKey(): string {
  return crypto.randomUUID();
}

export interface RequestOptions {
  method?: "GET" | "POST" | "PATCH" | "PUT" | "DELETE";
  /** JSON 으로 직렬화해 보낸다. FormData 를 주면 그대로 보내고 Content-Type 을 건드리지 않는다. */
  body?: unknown;
  query?: Record<string, string | number | boolean | null | undefined>;
  idempotencyKey?: string;
  signal?: AbortSignal;
  headers?: Record<string, string>;
}

export function buildUrl(path: string, query?: RequestOptions["query"]): string {
  const url = new URL(`${API_BASE_URL}${path}`);
  for (const [key, value] of Object.entries(query ?? {})) {
    if (value !== undefined && value !== null) url.searchParams.set(key, String(value));
  }
  return url.toString();
}

export function authHeaders(extra?: Record<string, string>): Record<string, string> {
  return {
    ...(authToken ? { Authorization: `Bearer ${authToken}` } : {}),
    ...extra,
  };
}

export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = "GET", body, query, idempotencyKey, signal, headers } = options;

  if (process.env.NODE_ENV !== "production") {
    const needsKey = method === "POST" && IDEMPOTENCY_REQUIRED.some((re) => re.test(path));
    if (needsKey && !idempotencyKey) {
      console.warn(
        `[api] ${method} ${path} 은 Idempotency-Key 가 필수다. newIdempotencyKey() 를 넘길 것 (없으면 서버가 400).`,
      );
    }
  }

  const isFormData = typeof FormData !== "undefined" && body instanceof FormData;

  let response: Response;
  try {
    response = await fetch(buildUrl(path, query), {
      method,
      signal,
      headers: authHeaders({
        ...(body !== undefined && !isFormData
          ? { "Content-Type": "application/json; charset=utf-8" }
          : {}),
        ...(idempotencyKey ? { "Idempotency-Key": idempotencyKey } : {}),
        ...headers,
      }),
      body: body === undefined ? undefined : isFormData ? body : JSON.stringify(body),
    });
  } catch (cause) {
    if (cause instanceof DOMException && cause.name === "AbortError") throw cause;
    throw new NetworkError(cause);
  }

  if (response.status === 204) return undefined as T;

  const text = await response.text();
  const payload: unknown = text ? JSON.parse(text) : null;

  if (!response.ok) {
    const envelope = payload as Partial<ApiErrorBody> | null;
    const error = envelope?.error;
    if (response.status === 401 && error?.code === "unauthenticated") onUnauthenticated?.();
    throw new ApiError(
      response.status,
      error?.code ?? "unknown",
      error?.message ?? `요청에 실패했어요 (${response.status})`,
      error?.detail,
    );
  }

  return payload as T;
}

export const api = {
  get: <T>(path: string, options?: Omit<RequestOptions, "method" | "body">) =>
    request<T>(path, { ...options, method: "GET" }),
  post: <T>(path: string, body?: unknown, options?: Omit<RequestOptions, "method" | "body">) =>
    request<T>(path, { ...options, method: "POST", body }),
  patch: <T>(path: string, body?: unknown, options?: Omit<RequestOptions, "method" | "body">) =>
    request<T>(path, { ...options, method: "PATCH", body }),
  put: <T>(path: string, body?: unknown, options?: Omit<RequestOptions, "method" | "body">) =>
    request<T>(path, { ...options, method: "PUT", body }),
  delete: <T>(path: string, options?: Omit<RequestOptions, "method" | "body">) =>
    request<T>(path, { ...options, method: "DELETE" }),
};
