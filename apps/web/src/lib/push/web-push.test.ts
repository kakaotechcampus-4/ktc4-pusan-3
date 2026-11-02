import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "@/lib/api/client";
import {
  askWebPush,
  forgetWebPushRegistration,
  shouldAskWebPush,
  syncWebPush,
} from "@/lib/push/web-push";
import { NO_REMINDER } from "@/lib/reminder";
import { useSessionStore } from "@/stores/session";

/**
 * 🚨 **권한 창은 처음 알림을 고를 때만 뜬다** (#609 · `docs/web/web-push-v1.md` 규칙). 브라우저는 한 번
 *    거절하면 다시 묻지 못해서, 앱을 켤 때 묻는 쪽으로 바뀌면 되돌릴 수 없다. e2e 는 웹 푸시를 끈 채
 *    돌아서 이 길을 지나지 않는다 — 여기서 Firebase 와 브라우저 API 를 흉내 내어 건다.
 */

const firebase = vi.hoisted(() => ({
  getToken: vi.fn<(...args: unknown[]) => Promise<string>>(),
}));

vi.mock("@/lib/env", async (original) => ({
  ...(await original<typeof import("@/lib/env")>()),
  FIREBASE_WEB: {
    apiKey: "key",
    projectId: "project",
    messagingSenderId: "123",
    appId: "app",
    vapidKey: "vapid",
  },
}));
vi.mock("firebase/app", () => ({ getApps: () => [], initializeApp: () => ({}) }));
vi.mock("firebase/messaging", () => ({
  getMessaging: () => ({}),
  getToken: firebase.getToken,
  isSupported: async () => true,
}));

class FakeNotification {
  static permission: NotificationPermission = "default";
  static requestPermission = vi.fn(async (): Promise<NotificationPermission> => {
    FakeNotification.permission = "granted";
    return "granted";
  });
}

const register = vi.fn(async () => ({ active: {} }));

beforeEach(() => {
  FakeNotification.permission = "default";
  FakeNotification.requestPermission.mockClear();
  firebase.getToken.mockReset().mockResolvedValue("fcm-token");
  register.mockClear();
  vi.stubGlobal("Notification", FakeNotification);
  vi.stubGlobal("window", { PushManager: class {}, Notification: FakeNotification });
  vi.stubGlobal("navigator", { serviceWorker: { register } });
  useSessionStore.setState({ token: "access" });
  forgetWebPushRegistration();
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  useSessionStore.setState({ token: null });
});

describe("권한을 묻는 때", () => {
  it("🚨 '없음' 을 고르면 묻지 않는다 — 무엇에 쓰는지 안 보이는 때다", () => {
    expect(shouldAskWebPush(NO_REMINDER)).toBe(false);
    expect(shouldAskWebPush("1h")).toBe(true);
  });

  it("이미 답한 브라우저에는 다시 묻지 않는다", () => {
    FakeNotification.permission = "granted";
    expect(shouldAskWebPush("1h")).toBe(false);
    FakeNotification.permission = "denied";
    expect(shouldAskWebPush("1h")).toBe(false);
  });

  it("🚨 앱을 켤 때(syncWebPush)는 묻지 않고, 허용 전이면 아무것도 하지 않는다", async () => {
    const put = vi.spyOn(api, "put");
    await syncWebPush();

    expect(FakeNotification.requestPermission).not.toHaveBeenCalled();
    expect(firebase.getToken).not.toHaveBeenCalled();
    expect(put).not.toHaveBeenCalled();
  });

  it("알림을 고를 때(askWebPush) 묻고, 허용하면 이 로그인의 기기로 등록한다", async () => {
    const put = vi.spyOn(api, "put");
    await askWebPush();

    expect(FakeNotification.requestPermission).toHaveBeenCalledTimes(1);
    expect(register).toHaveBeenCalledWith("/push-sw.js", { scope: "/push/" });
    expect(put).toHaveBeenCalledWith("/me/push-device", { token: "fcm-token", platform: "web" });
  });

  it("거절하면 등록하지 않는다", async () => {
    FakeNotification.requestPermission.mockImplementationOnce(async () => {
      FakeNotification.permission = "denied";
      return "denied";
    });
    const put = vi.spyOn(api, "put");
    await askWebPush();

    expect(firebase.getToken).not.toHaveBeenCalled();
    expect(put).not.toHaveBeenCalled();
  });
});

describe("기기 등록", () => {
  beforeEach(() => {
    FakeNotification.permission = "granted";
  });

  it("동시에 두 번 불러도 토큰 받기와 등록은 한 번이다", async () => {
    const put = vi.spyOn(api, "put");
    await Promise.all([syncWebPush(), syncWebPush()]);

    expect(firebase.getToken).toHaveBeenCalledTimes(1);
    expect(put).toHaveBeenCalledTimes(1);
  });

  it("같은 로그인에 같은 토큰은 다시 보내지 않고, 토큰이 바뀌면 보낸다", async () => {
    const put = vi.spyOn(api, "put");
    await syncWebPush();
    await syncWebPush();
    expect(put).toHaveBeenCalledTimes(1);

    firebase.getToken.mockResolvedValue("rotated-token");
    await syncWebPush();
    expect(put).toHaveBeenLastCalledWith("/me/push-device", {
      token: "rotated-token",
      platform: "web",
    });
  });

  it("로그아웃하고 다시 로그인하면 같은 토큰이어도 다시 보낸다 — 서버가 기기를 지웠다", async () => {
    const put = vi.spyOn(api, "put");
    await syncWebPush();
    forgetWebPushRegistration();
    await syncWebPush();

    expect(put).toHaveBeenCalledTimes(2);
  });

  it("🚨 토큰을 받는 사이 로그아웃했으면 보내지 않는다", async () => {
    firebase.getToken.mockImplementationOnce(async () => {
      useSessionStore.setState({ token: null });
      return "fcm-token";
    });
    const put = vi.spyOn(api, "put");
    await syncWebPush();

    expect(put).not.toHaveBeenCalled();
  });
});
