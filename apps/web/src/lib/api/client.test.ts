import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  api,
  refreshSession,
  setAuthToken,
  setSessionRefreshedHandler,
  setUnauthenticatedHandler,
} from "@/lib/api/client";
import { isApiError, NetworkError } from "@/lib/api/errors";
import { idempotentPath, newIdempotencyKey } from "@/lib/api/idempotency";
import { streamRunEvents } from "@/lib/api/sse";
import type { Me } from "@/lib/api/types";
import { MOCK_REFRESH_KEY } from "@/mocks/handlers/auth";
import { setScenario } from "@/mocks/scenario";
import { server } from "@/mocks/server";

/**
 * access 는 메모리, refresh 는 쿠키 (#465 · docs/web/auth-consent-v1.md §2).
 *
 * 🚨 여기서 거는 것은 **언제 세션을 버리는가** 다. 만료된 access 나 끊긴 연결로 세션을 버리면
 *    보호자는 쓰던 중에 로그인 화면으로 튕기고, 반대로 서버가 끝낸 세션을 들고 있으면 화면이
 *    401 만 받는다.
 */

/** 이 테스트가 보낸 요청의 경로를 순서대로 모은다 (`/api/v1` 아래). */
function recordPaths(): { paths: string[]; credentials: Map<string, RequestCredentials> } {
  const paths: string[] = [];
  const credentials = new Map<string, RequestCredentials>();
  server.events.on("request:start", ({ request }) => {
    const path = new URL(request.url).pathname.replace(/^\/api\/v1/, "");
    paths.push(path);
    credentials.set(path, request.credentials);
  });
  return { paths, credentials };
}

/** 목의 refresh 쿠키 표식. 교환 · 가입이 심는 것과 같다 (`mocks/handlers/auth.ts`). */
function giveRefreshCookie(): void {
  localStorage.setItem(MOCK_REFRESH_KEY, "1");
}

const onUnauthenticated = vi.fn();
const onRefreshed = vi.fn();

beforeEach(() => {
  setUnauthenticatedHandler(onUnauthenticated);
  setSessionRefreshedHandler(onRefreshed);
});

afterEach(() => {
  server.events.removeAllListeners();
  setUnauthenticatedHandler(null);
  setSessionRefreshedHandler(null);
  setAuthToken(null);
  setScenario("default");
  onUnauthenticated.mockReset();
  onRefreshed.mockReset();
});

describe("refreshSession", () => {
  it("쿠키가 살아 있으면 새 access 를 받아 스토어에 넘긴다", async () => {
    giveRefreshCookie();

    const session = await refreshSession();

    expect(session).toMatchObject({ expiresIn: 900 });
    expect(onRefreshed).toHaveBeenCalledWith(session);
  });

  it("서버가 거절하면(쿠키 없음 · 만료 · 로그아웃) null 이다 — 던지지 않는다", async () => {
    await expect(refreshSession()).resolves.toBeNull();
    expect(onRefreshed).not.toHaveBeenCalled();
  });

  it("🚨 서버에 닿지 못하면 던진다 — 로그아웃(null)과 섞이면 연결이 끊길 때마다 세션을 버린다", async () => {
    giveRefreshCookie();
    setScenario("session_unreachable");

    await expect(refreshSession()).rejects.toBeInstanceOf(NetworkError);
  });

  it("🚨 기다리는 사이 로그아웃했으면 받은 토큰을 버린다 — 늦게 온 응답이 세션을 되살리지 않는다", async () => {
    giveRefreshCookie();

    const pending = refreshSession();
    setAuthToken(null); // 로그아웃 (`signOut`)

    await expect(pending).resolves.toBeNull();
    expect(onRefreshed).not.toHaveBeenCalled();
  });

  it("🚨 동시에 불러도 한 번만 나간다 — 회전된 refresh 가 늦게 또 가면 서버가 탈취로 보고 세션을 지운다", async () => {
    giveRefreshCookie();
    const { paths } = recordPaths();

    const [first, second] = await Promise.all([refreshSession(), refreshSession()]);

    expect(paths.filter((p) => p === "/auth/refresh")).toHaveLength(1);
    expect(second).toBe(first);
  });
});

describe("request — 401 뒤 갱신", () => {
  it("만료된 access 로 401 을 받으면 갱신하고 한 번 다시 보낸다 — 화면은 튕기지 않는다", async () => {
    giveRefreshCookie();
    setScenario("session_expired");
    // 앱을 켤 때 받은 access 가 이미 만료돼 있다 (목이 처음 주는 토큰).
    await refreshSession();
    const { paths } = recordPaths();

    const me = await api.get<Me>("/me");

    expect(me.children.length).toBeGreaterThan(0);
    expect(paths).toEqual(["/me", "/auth/refresh", "/me"]);
    expect(onUnauthenticated).not.toHaveBeenCalled();
  });

  it("갱신까지 거절되면 전역 처리로 넘기고 401 을 던진다", async () => {
    giveRefreshCookie();
    setScenario("session_expired");
    await refreshSession();
    // 그 사이 다른 기기에서 탈퇴했거나 로그아웃했다 — 서버에 refresh 가 없다.
    localStorage.removeItem(MOCK_REFRESH_KEY);

    const caught = await api.get("/me").catch((e: unknown) => e);

    expect(isApiError(caught, "unauthenticated")).toBe(true);
    expect(onUnauthenticated).toHaveBeenCalledTimes(1);
  });

  it("🚨 전역 처리를 끈 화면(탈퇴)도 갱신은 한다 — 끄는 것은 '로그인 화면으로 보내기' 뿐이다", async () => {
    giveRefreshCookie();
    setScenario("session_expired");
    await refreshSession();

    const me = await api.get<Me>("/me", { handleUnauthenticated: false });

    expect(me.children.length).toBeGreaterThan(0);
  });
});

describe("request — 보내기 전", () => {
  it("곧 만료될 access 는 보내기 전에 갱신한다 — 401 왕복을 한 번 아낀다", async () => {
    giveRefreshCookie();
    setAuthToken("about-to-expire", Date.now() + 1_000);
    const { paths } = recordPaths();

    await api.get<Me>("/me");

    expect(paths).toEqual(["/auth/refresh", "/me"]);
  });

  it("앱을 켠 직후 갱신이 도는 중이면 그것을 기다렸다가 새 토큰으로 보낸다", async () => {
    giveRefreshCookie();
    const sent: Array<string | null> = [];
    server.events.on("request:start", ({ request }) => {
      if (request.url.endsWith("/me")) sent.push(request.headers.get("Authorization"));
    });

    // 갱신이 끝나기 전에 화면이 쿼리를 보낸다 — 토큰이 아직 없다.
    const booting = refreshSession();
    const me = api.get<Me>("/me");
    const session = await booting;
    await me;

    // 기다리지 않으면 토큰 없이 나가서 실서버에서는 401 을 맞는다.
    expect(sent).toEqual([`Bearer ${session?.token}`]);
  });

  it("🚨 refresh 쿠키는 /auth/* 에만 실린다 — 일반 API 는 Bearer 뿐이다", async () => {
    giveRefreshCookie();
    await refreshSession();
    const { credentials } = recordPaths();

    await api.get<Me>("/me");
    await api.post("/auth/logout");

    expect(credentials.get("/me")).toBe("same-origin");
    expect(credentials.get("/auth/logout")).toBe("include");
  });
});

describe("SSE — 401 뒤 갱신", () => {
  /** run 하나를 만들고, 만료된 access 를 쥔 상태로 만든다 (목이 처음 주는 토큰). */
  async function runWithExpiredToken(): Promise<string> {
    const { run_id } = await api.post<{ run_id: string }>(
      idempotentPath.input("c1"),
      { text: "지어낸 한 줄", source: "home_input" },
      { idempotencyKey: newIdempotencyKey() },
    );
    giveRefreshCookie();
    setScenario("session_expired");
    await refreshSession();
    return run_id;
  }

  async function drain(runId: string): Promise<void> {
    for await (const event of streamRunEvents(runId)) void event;
  }

  it("만료된 access 로 연결하면 갱신하고 한 번 다시 연결한다", async () => {
    const runId = await runWithExpiredToken();
    const { paths } = recordPaths();

    await drain(runId);

    const events = `/runs/${runId}/events`;
    expect(paths).toEqual([events, "/auth/refresh", events]);
    expect(onUnauthenticated).not.toHaveBeenCalled();
  });

  it("갱신까지 거절되면 전역 처리로 넘기고 연결 실패로 끝난다", async () => {
    const runId = await runWithExpiredToken();
    localStorage.removeItem(MOCK_REFRESH_KEY);

    await expect(drain(runId)).rejects.toBeInstanceOf(NetworkError);
    expect(onUnauthenticated).toHaveBeenCalledTimes(1);
  });
});
