import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "@/lib/api/client";
import { registerShellToken, shouldAskWebPush, syncWebPush } from "@/lib/push/web-push";
import { useSessionStore } from "@/stores/session";

/**
 * 🚨 **Firebase 웹 값이 비어 있으면 앱에서도 기기를 등록하지 않는다** (#609 FE-4 · `docs/web/web-push-v1.md` "앱").
 *    기기 토큰 수집 동의가 나가기 전에는 운영에서 등록을 켜지 않는데(#605), 그 스위치는 웹 배포 한 곳이다 —
 *    앱에 `google-services.json` 이 들어 있어도 이 스위치가 꺼져 있으면 토큰이 서버로 가지 않는다.
 */
vi.mock("@/lib/env", async (original) => ({
  ...(await original<typeof import("@/lib/env")>()),
  FIREBASE_WEB: null,
}));

const bridge = { available: true, ready: vi.fn(), sync: vi.fn(), ask: vi.fn() };

beforeEach(() => {
  vi.stubGlobal("window", { icatch: { push: bridge } });
  vi.stubGlobal("navigator", {});
  useSessionStore.setState({ token: "access" });
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  useSessionStore.setState({ token: null });
});

describe("스위치가 꺼진 앱", () => {
  it("알림을 골라도 묻지 않고, 앱을 켜도 토큰을 부탁하지 않는다", async () => {
    expect(shouldAskWebPush("1h")).toBe(false);
    await syncWebPush();

    expect(bridge.sync).not.toHaveBeenCalled();
    expect(bridge.ask).not.toHaveBeenCalled();
  });

  it("셸이 토큰을 넘겨도 등록하지 않는다", async () => {
    const put = vi.spyOn(api, "put");
    await registerShellToken("device-token");

    expect(put).not.toHaveBeenCalled();
  });
});
