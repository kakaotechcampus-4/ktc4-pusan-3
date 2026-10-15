import { API_BASE_URL } from "@/lib/env";
import { ApiError, NetworkError, type ApiErrorBody } from "./errors";
import {
  IdempotencyKeyRequiredError,
  requiresIdempotencyKey,
  type IdempotencyKey,
} from "./idempotency";
import type { AuthRefreshResponse } from "./types";

/* ── 인증 토큰 ────────────────────────────────────────────────────────────
 * Authorization: Bearer <access token> — 예외 없음 (NF-09). 로그인 · 갱신 경로만 쿠키를 쓴다.
 * 여기는 "지금 값" 만 들고 있는다. 넣는 쪽은 세션 스토어다.
 * (스토어를 직접 import 하면 서버 컴포넌트에서 client.ts 를 못 쓰게 된다.)
 *
 * 🚨 access 토큰은 **메모리에만 있다.** 앱을 다시 켜면 비어 있고, refresh 쿠키로 다시 받는다
 *    (docs/web/auth-consent-v1.md §2).
 */
let authToken: string | null = null;
/** epoch ms. 모르면 null — 그때는 미리 갱신하지 않고 401 을 맞은 뒤 갱신한다. */
let authExpiresAt: number | null = null;

/**
 * 로그아웃할 때마다 올라간다. 🚨 그 전에 나간 갱신의 응답은 버린다 — 로그아웃 요청과 갱신이 겹치면
 * 서버가 갱신을 먼저 받아 새 토큰을 줄 수 있고, 그 응답이 로그아웃 뒤에 도착해 세션을 되살린다.
 */
let sessionEpoch = 0;

export function setAuthToken(token: string | null, expiresAt: number | null = null): void {
  if (token === null) sessionEpoch += 1;
  authToken = token;
  authExpiresAt = token ? expiresAt : null;
}

/* ── 401 unauthenticated ──────────────────────────────────────────────────
 * 세션이 끝난 것을 한 곳에서 처리한다 — 화면마다 401 을 다루면 어딘가는 빠뜨린다.
 * 스토어를 여기서 import 하지 않는 이유는 위와 같다(서버 컴포넌트에서 못 쓰게 된다).
 * 그래서 Providers 가 핸들러를 꽂아 준다.
 *
 * 🚨 여기까지 오는 401 은 **refresh 까지 실패한 것**이다. access 만료는 아래 갱신이 먼저 받는다.
 * 🚨 `invalid_handoff`(401) 는 여기 걸리지 않는다. 그건 로그인 교환이 실패한 것이라
 *    "세션이 끊겼다" 와 다른 상태고, 콜백 화면이 자기 문구로 처리한다.
 */
let onUnauthenticated: (() => void) | null = null;

export function setUnauthenticatedHandler(handler: (() => void) | null): void {
  onUnauthenticated = handler;
}

/** 세션이 끝났다고 알린다. `request` 밖에서 같은 판정을 내린 곳(SSE)이 쓴다. */
export function notifyUnauthenticated(): void {
  onUnauthenticated?.();
}

/* ── 세션 갱신 (refresh) ──────────────────────────────────────────────────
 * `POST /auth/refresh` 는 Bearer 가 아니라 서버가 심은 HttpOnly 쿠키로 인증한다. 성공하면 새
 * access 토큰을 주고 쿠키를 회전시킨다. 정본은 docs/web/auth-consent-v1.md §2.
 *
 * 🚨 **동시에 한 번만 나간다** (single-flight). 화면 하나가 쿼리 여러 개를 동시에 보내 401 을
 *    여럿 받아도 갱신은 하나다. 서버는 이미 회전된 refresh 가 늦게 다시 오면 탈취로 보고 세션을
 *    지운다 — 여기서 여러 번 보내면 우리 스스로 로그아웃을 만든다.
 */
const REFRESH_PATH = "/auth/refresh";
/** 만료까지 이만큼 남았으면 보내기 전에 미리 갱신한다. 401 왕복 한 번을 아낀다. */
const REFRESH_AHEAD_MS = 60_000;

export interface RefreshedSession {
  token: string;
  /** 초. */
  expiresIn: number;
}

let onRefreshed: ((session: RefreshedSession) => void) | null = null;

/** 갱신으로 받은 토큰을 세션 스토어가 받아 간다 (만료 시각을 같이 든다). */
export function setSessionRefreshedHandler(
  handler: ((session: RefreshedSession) => void) | null,
): void {
  onRefreshed = handler;
}

let refreshing: Promise<RefreshedSession | null> | null = null;

/**
 * refresh 쿠키로 새 access 토큰을 받는다.
 *
 * - 받으면 그 세션, 서버가 거절하면(`401`) `null` 이다. 쿠키가 없거나 만료 · 로그아웃 · 탈퇴다
 * - 🚨 **서버에 닿지 못하면 던진다** (`NetworkError` · 5xx 는 `ApiError`). 로그아웃이 아니다 —
 *   쿠키는 그대로라 연결이 돌아오면 다시 받을 수 있다. 부르는 쪽이 세션을 버리지 않게 갈라 둔다
 */
export function refreshSession(): Promise<RefreshedSession | null> {
  refreshing ??= requestRefresh().finally(() => {
    refreshing = null;
  });
  return refreshing;
}

async function requestRefresh(): Promise<RefreshedSession | null> {
  const epoch = sessionEpoch;
  let response: Response;
  try {
    // 🚨 `credentials: "include"` 가 없으면 쿠키가 안 실리고, 응답의 새 쿠키도 브라우저가 버린다.
    response = await fetch(buildUrl(REFRESH_PATH), { method: "POST", credentials: "include" });
  } catch (cause) {
    throw new NetworkError(cause);
  }
  if (response.status === 401) return null;
  if (!response.ok) {
    throw new ApiError(response.status, "unknown", `세션을 확인하지 못했어요 (${response.status})`);
  }

  const body = (await response.json()) as AuthRefreshResponse;
  // 기다리는 사이 로그아웃했다 — 받은 토큰을 쓰지 않는다 (위 `sessionEpoch`).
  if (epoch !== sessionEpoch) return null;
  const session: RefreshedSession = { token: body.token, expiresIn: body.expires_in };
  setAuthToken(session.token, Date.now() + session.expiresIn * 1000);
  onRefreshed?.(session);
  return session;
}

/**
 * 보내기 전에 토큰을 챙긴다.
 *
 * - 갱신이 이미 나가 있으면 기다린다 — 앱을 켠 직후에는 토큰이 아직 없고 갱신만 돌고 있다
 * - 곧 만료면 미리 갱신한다. 실패해도 여기서는 삼키고 그대로 보낸다 — 401 이면 뒤에서 다시 판단한다
 */
async function prepareToken(): Promise<void> {
  if (refreshing) await refreshing.catch(() => null);
  if (authToken === null || authExpiresAt === null) return;
  if (authExpiresAt - Date.now() > REFRESH_AHEAD_MS) return;
  await refreshSession().catch(() => null);
}

/**
 * 401 을 받은 뒤 다시 보낼 토큰이 생겼는가.
 *
 * 🚨 보낸 사이에 토큰이 바뀌었으면(다른 요청이 먼저 갱신했거나 다시 로그인했다) 갱신하지 않고
 *    새 토큰으로 다시 보낸다. 같으면 갱신한다.
 * 🚨 갱신이 서버에 닿지 못했으면 **던진다.** 원래 요청이 연결 문제로 실패한 것과 같게 다룬다 —
 *    `false` 로 돌려 401 로 끝내면 전역 처리가 이 기기의 세션을 버린다.
 */
async function renewAfterRejection(sentToken: string | null): Promise<boolean> {
  if (authToken !== null && authToken !== sentToken) return true;
  return (await refreshSession()) !== null;
}

/** `401 unauthenticated` 인가. 봉투를 읽어야 해서 복제본을 읽는다 (본문은 부르는 쪽 몫이다). */
async function isSessionRejected(response: Response): Promise<boolean> {
  if (response.status !== 401) return false;
  try {
    const envelope = (await response.clone().json()) as Partial<ApiErrorBody> | null;
    return envelope?.error?.code === "unauthenticated";
  } catch {
    return false;
  }
}

async function send(url: string, init: RequestInit): Promise<Response> {
  try {
    return await fetch(url, init);
  } catch (cause) {
    if (cause instanceof DOMException && cause.name === "AbortError") throw cause;
    throw new NetworkError(cause);
  }
}

/**
 * Bearer 가 붙는 요청을 보낸다. 토큰을 챙기고, `401 unauthenticated` 면 한 번 갱신해 다시 보낸다.
 *
 * 🚨 다시 보내는 것은 **한 번뿐이다.** 그래도 401 이면 그 응답을 돌려준다 — 판정은 부르는 쪽이다.
 * 🚨 `init` 을 함수로 받는다. 다시 보낼 때 새 토큰으로 헤더를 다시 만들어야 한다.
 *    되돌릴 수 없는 요청도 다시 보내도 된다 — 서버는 인증에서 막고 아무것도 실행하지 않았다.
 */
export async function authorizedFetch(url: string, init: () => RequestInit): Promise<Response> {
  await prepareToken();
  const sentToken = authToken;
  const first = await send(url, init());
  if (!(await isSessionRejected(first))) return first;
  if (!(await renewAfterRejection(sentToken))) return first;
  return send(url, init());
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
  /**
   * `false` 면 `401 unauthenticated` 를 위의 전역 처리에 넘기지 않고 호출자에게 던지기만 한다.
   *
   * 🚨 **탈퇴 화면 말고는 쓰지 않는다** (#389). 탈퇴는 커밋하면서 그 보호자의 세션을 전부
   *    지운다 — 응답만 잃은 뒤의 401 은 만료가 아니라 **탈퇴가 끝났다**는 뜻이다. 전역 처리에
   *    넘기면 돌아올 주소로 탈퇴 화면을 기억한 채 `/` 로 튕겨서 "탈퇴했어요" 를 아무도 못 본다.
   *    다른 화면이 이걸 끄면 만료된 세션이 그 화면에 남는다.
   *    ⚠️ 살아 있는 세션의 401 이 늘 탈퇴는 아니다 (다른 탭의 로그아웃) — 한계는 그 화면 주석에 있다.
   */
  handleUnauthenticated?: boolean;
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
  const {
    method = "GET",
    body,
    query,
    idempotencyKey,
    signal,
    headers,
    handleUnauthenticated = true,
  } = options;

  // 🚨 경고가 아니라 차단이다. 환경을 가리지 않고, 요청은 나가지 않는다.
  //    메서드도 표가 정한다 — 수정 초안 반영(`PATCH`)도 되돌릴 수 없다 (#316).
  if (!idempotencyKey && requiresIdempotencyKey(method, path)) {
    throw new IdempotencyKeyRequiredError(path);
  }

  const isFormData = typeof FormData !== "undefined" && body instanceof FormData;

  const response = await authorizedFetch(buildUrl(path, query), () => ({
    method,
    signal,
    // 🚨 로그인 · 갱신 · 로그아웃 · 탈퇴의 응답이 refresh 쿠키를 심고 지운다. 쿠키의 Path 도
    //    `/api/v1/auth` 라 그 밖의 요청에는 실리지 않는다 — 일반 API 는 Bearer 뿐이다.
    credentials: path.startsWith("/auth/") ? "include" : "same-origin",
    headers: authHeaders({
      ...(body !== undefined && !isFormData
        ? { "Content-Type": "application/json; charset=utf-8" }
        : {}),
      ...(idempotencyKey ? { "Idempotency-Key": idempotencyKey } : {}),
      ...headers,
    }),
    body: body === undefined ? undefined : isFormData ? body : JSON.stringify(body),
  }));

  if (response.status === 204) return undefined as T;

  const text = await response.text();
  const payload: unknown = text ? JSON.parse(text) : null;

  if (!response.ok) {
    const envelope = payload as Partial<ApiErrorBody> | null;
    const error = envelope?.error;
    if (response.status === 401 && error?.code === "unauthenticated" && handleUnauthenticated) {
      onUnauthenticated?.();
    }
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
