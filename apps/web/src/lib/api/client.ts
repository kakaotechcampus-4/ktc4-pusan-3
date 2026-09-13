import { API_BASE_URL } from "@/lib/env";
import { ApiError, NetworkError, type ApiErrorBody } from "./errors";
import {
  IdempotencyKeyRequiredError,
  requiresIdempotencyKey,
  type IdempotencyKey,
} from "./idempotency";

/* ── 인증 토큰 ────────────────────────────────────────────────────────────
 * Authorization: Bearer <token> — 예외 없음. 인증 없는 엔드포인트는 없다 (NF-09).
 * 저장 위치는 세션 스토어가 정하고, 여기는 "지금 값" 만 들고 있는다.
 * (스토어를 직접 import 하면 서버 컴포넌트에서 client.ts 를 못 쓰게 된다.)
 */
let authToken: string | null = null;

export function setAuthToken(token: string | null): void {
  authToken = token;
}

/* ── Idempotency-Key ──────────────────────────────────────────────────────
 * 어느 엔드포인트가 키를 요구하는지는 idempotency.ts 의 표가 정본이다.
 * 여기서는 그 표를 마지막 그물로만 쓴다 — 정상 경로는 operations.ts 의 전용 함수다.
 */
export interface RequestOptions {
  method?: "GET" | "POST" | "PATCH" | "PUT" | "DELETE";
  /** JSON 으로 직렬화해 보낸다. FormData 를 주면 그대로 보내고 Content-Type 을 건드리지 않는다. */
  body?: unknown;
  query?: Record<string, string | number | boolean | null | undefined>;
  /** 🚨 되돌릴 수 없는 엔드포인트에서는 없으면 요청 자체가 막힌다 (idempotency.ts). */
  idempotencyKey?: IdempotencyKey;
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

  // 🚨 경고가 아니라 차단이다. 환경을 가리지 않고, 요청은 나가지 않는다.
  if (method === "POST" && !idempotencyKey && requiresIdempotencyKey(path)) {
    throw new IdempotencyKeyRequiredError(path);
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
