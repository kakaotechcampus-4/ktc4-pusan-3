import { afterEach, describe, expect, it } from "vitest";

import { authHeaders } from "@/lib/api/client";
import { MOCK_REFRESH_KEY } from "@/mocks/handlers/auth";
import { setScenario } from "@/mocks/scenario";
import { hasLiveSession, useSessionStore } from "@/stores/session";

/**
 * 세션 스토어 (#465 · docs/web/auth-consent-v1.md §2).
 *
 * 🚨 토큰은 메모리에만 있고, 앱을 다시 켜면 refresh 쿠키로 되살린다. "확인하지 못함" 과
 *    "로그인 전" 이 갈라져야 연결이 끊긴 보호자를 로그인 화면으로 보내지 않는다.
 */

const PERSIST_KEY = "icatch.active-child";

afterEach(() => {
  useSessionStore.getState().signOut();
  useSessionStore.setState({ hydrated: false, restoreFailed: false });
  localStorage.removeItem(PERSIST_KEY);
  setScenario("default");
});

describe("restore — 앱을 켤 때", () => {
  it("refresh 쿠키가 있으면 로그인된 채로 끝난다", async () => {
    localStorage.setItem(MOCK_REFRESH_KEY, "1");

    await useSessionStore.getState().restore();

    const state = useSessionStore.getState();
    expect(state).toMatchObject({ hydrated: true, restoreFailed: false });
    expect(hasLiveSession(state)).toBe(true);
    // 받은 토큰이 클라이언트에도 들어가 다음 요청에 실린다.
    expect(authHeaders().Authorization).toBe(`Bearer ${state.token}`);
  });

  it("쿠키가 없으면 로그인 전이다 — 확인 실패가 아니다", async () => {
    await useSessionStore.getState().restore();

    const state = useSessionStore.getState();
    expect(state).toMatchObject({ hydrated: true, restoreFailed: false });
    expect(hasLiveSession(state)).toBe(false);
  });

  it("🚨 서버에 닿지 못하면 로그아웃이 아니라 '확인하지 못함' 이다", async () => {
    localStorage.setItem(MOCK_REFRESH_KEY, "1");
    setScenario("session_unreachable");

    await useSessionStore.getState().restore();

    expect(useSessionStore.getState()).toMatchObject({ hydrated: true, restoreFailed: true });

    // 연결이 돌아와 다시 시도하면 그대로 이어진다 — 쿠키는 남아 있었다.
    setScenario("default");
    await useSessionStore.getState().restore();
    expect(useSessionStore.getState()).toMatchObject({ hydrated: true, restoreFailed: false });
    expect(hasLiveSession(useSessionStore.getState())).toBe(true);
  });
});

describe("저장소", () => {
  it("🚨 토큰은 어디에도 쓰지 않는다 — 남기는 것은 마지막에 본 아이뿐이다", () => {
    useSessionStore.getState().signIn("secret-access-token", 900);
    useSessionStore.getState().setActiveChild("c1");

    const stored = localStorage.getItem(PERSIST_KEY) ?? "";
    expect(stored).toContain("c1");
    expect(stored).not.toContain("secret-access-token");
  });

  it("🚨 access 가 만료돼도 로그아웃으로 보지 않는다 — 갱신은 클라이언트가 한다", () => {
    useSessionStore.getState().signIn("token", 0);

    expect(hasLiveSession(useSessionStore.getState())).toBe(true);
  });
});
