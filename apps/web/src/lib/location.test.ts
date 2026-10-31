import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  askDeviceLocation,
  checkDeviceLocation,
  coarseLocation,
  coarsen,
  nextPermission,
  openDeviceLocationSettings,
  readCoarseLocation,
} from "./location";
import { LOCATION_EVENT, readLocationEvent, type LocationBridge } from "./native/location";

/**
 * 흐린 위치 (#593) — 서버로 가는 좌표가 둘째 자리인가, 입력을 보낼 때 묻지 않는가.
 *
 * 셸은 `window.icatch.location` 을 꽂고 `icatch:location` 으로 답하는 가짜다 (셸 쪽 판정은 `apps/mobile` 의 `location.test.ts`).
 */

function fakeShell(answer: Partial<Record<"check" | "ask" | "read", unknown>>) {
  const calls: string[] = [];
  const reply = (action: "check" | "ask" | "read") => () => {
    calls.push(action);
    if (!(action in answer)) return;
    window.dispatchEvent(new CustomEvent(LOCATION_EVENT, { detail: answer[action] }));
  };
  const bridge: LocationBridge = {
    available: true,
    check: reply("check"),
    ask: reply("ask"),
    read: reply("read"),
    openSettings: () => calls.push("settings"),
  };
  window.icatch = { ...window.icatch, location: bridge };
  return calls;
}

// vitest 가 node 환경이라 `window` 가 없다. 셸이 이벤트를 쏘는 자리만 있으면 되므로 `EventTarget` 으로 세운다.
beforeEach(() => {
  vi.stubGlobal("window", new EventTarget());
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("🚨 서버로 가는 좌표는 소수점 둘째 자리다", () => {
  it("반올림해서 둘째 자리까지만 남긴다", () => {
    expect(coarseLocation(35.233_456_7, 129.081_234_5)).toEqual({ lat: 35.23, lon: 129.08 });
    expect(coarseLocation(-33.868_8, 151.209_3)).toEqual({ lat: -33.87, lon: 151.21 });
    for (const value of [35.123_456, 129.999_9, -0.004, 0.005_1]) {
      expect(String(coarsen(value)).split(".")[1]?.length ?? 0).toBeLessThanOrEqual(2);
    }
  });

  it("숫자가 아니거나 범위를 벗어나면 싣지 않는다", () => {
    expect(coarseLocation(Number.NaN, 129)).toBeNull();
    expect(coarseLocation(91, 129)).toBeNull();
    expect(coarseLocation(35, -181)).toBeNull();
  });

  it("🚨 셸이 덜 흐려 보내도 웹이 한 번 더 줄인다", async () => {
    fakeShell({ read: { type: "position", lat: 35.123_456, lon: 129.987_654 } });
    expect(await readCoarseLocation()).toEqual({ lat: 35.12, lon: 129.99 });
  });
});

describe("셸이 보낸 이벤트를 다시 본다", () => {
  it("모양이 다르면 버린다", () => {
    for (const detail of [
      null,
      "position",
      { type: "position", lat: "35", lon: 129 },
      { type: "position", lat: 35, lon: Number.POSITIVE_INFINITY },
      { type: "position", lat: 95, lon: 129 },
      { type: "permission", action: "check", status: "maybe" },
      { type: "permission", status: "granted" },
      { type: "unavailable", reason: "denied" },
    ]) {
      expect(readLocationEvent(detail)).toBeNull();
    }
  });
});

describe("앱 안 — 셸 창구", () => {
  it("허용하지 않았으면 좌표 없이 간다 · 묻지 않는다", async () => {
    const calls = fakeShell({ read: { type: "unavailable", reason: "not-granted" } });
    expect(await readCoarseLocation()).toBeNull();
    expect(calls).toEqual(["read"]);
  });

  it("셸이 답하지 않으면 기다리다 좌표 없이 간다", async () => {
    vi.useFakeTimers();
    fakeShell({});
    const pending = readCoarseLocation();
    await vi.advanceTimersByTimeAsync(2_000);
    expect(await pending).toBeNull();
  });

  it("권한 상태 확인과 묻기는 셸의 답 그대로다", async () => {
    fakeShell({
      check: { type: "permission", action: "check", status: "prompt" },
      ask: { type: "permission", action: "ask", status: "declined" },
    });
    expect(await checkDeviceLocation()).toBe("prompt");
    expect(await askDeviceLocation()).toBe("declined");
  });

  it("다른 부탁의 답은 이 부탁의 답으로 받지 않는다", async () => {
    vi.useFakeTimers();
    fakeShell({ read: { type: "permission", action: "check", status: "granted" } });
    const pending = readCoarseLocation();
    await vi.advanceTimersByTimeAsync(2_000);
    expect(await pending).toBeNull();
  });

  it("🚨 권한 창이 떠 있는 동안 온 확인의 답을 묻기의 답으로 받지 않는다 (#593 실기기)", async () => {
    fakeShell({ check: { type: "permission", action: "check", status: "prompt" } });
    const asking = askDeviceLocation();
    // 권한 창 뒤에서 10 설정의 안내가 상태를 확인한다. 셸은 확인부터 답한다.
    expect(await checkDeviceLocation()).toBe("prompt");
    window.dispatchEvent(
      new CustomEvent(LOCATION_EVENT, {
        detail: { type: "permission", action: "ask", status: "declined" },
      }),
    );
    expect(await asking).toBe("declined");
  });

  it("설정 화면은 앱 안에서만 연다", () => {
    expect(openDeviceLocationSettings()).toBe(false);
    const calls = fakeShell({});
    expect(openDeviceLocationSettings()).toBe(true);
    expect(calls).toEqual(["settings"]);
  });
});

describe("브라우저", () => {
  function stubBrowser(state: "granted" | "prompt" | "denied" | null) {
    const getCurrentPosition = vi.fn<Geolocation["getCurrentPosition"]>((ok) =>
      ok({ coords: { latitude: 37.566_535, longitude: 126.977_969 } } as GeolocationPosition),
    );
    vi.stubGlobal("navigator", {
      ...navigator,
      geolocation: { getCurrentPosition },
      permissions:
        state === null ? undefined : { query: vi.fn(async () => ({ state }) as PermissionStatus) },
    });
    return getCurrentPosition;
  }

  it("허용된 브라우저는 흐린 좌표를 낮은 정확도로 읽는다", async () => {
    const getCurrentPosition = stubBrowser("granted");
    expect(await readCoarseLocation()).toEqual({ lat: 37.57, lon: 126.98 });
    expect(getCurrentPosition.mock.calls[0]?.[2]).toMatchObject({ enableHighAccuracy: false });
  });

  it("권한 상태를 볼 수 없는 브라우저는 묻지도 안내하지도 않는다 — 허용받아도 실을 수 없다", async () => {
    const getCurrentPosition = stubBrowser(null);
    expect(await checkDeviceLocation()).toBe("unsupported");
    expect(await askDeviceLocation()).toBe("unsupported");
    expect(getCurrentPosition).not.toHaveBeenCalled();
  });

  it("🚨 위치 창구가 없는 앱 안에서는 브라우저 길로 빠지지 않는다 — 웹뷰는 위치가 꺼져 있다", async () => {
    const getCurrentPosition = stubBrowser("granted");
    Object.assign(window, { ReactNativeWebView: { postMessage() {} } });
    expect(await checkDeviceLocation()).toBe("unsupported");
    expect(await askDeviceLocation()).toBe("unsupported");
    expect(await readCoarseLocation()).toBeNull();
    expect(getCurrentPosition).not.toHaveBeenCalled();
  });

  it("🚨 허용 전이거나 권한을 볼 수 없으면 읽지 않는다 — 읽으면 입력을 보내다가 권한 창이 뜬다", async () => {
    for (const state of ["prompt", "denied", null] as const) {
      const getCurrentPosition = stubBrowser(state);
      expect(await readCoarseLocation()).toBeNull();
      expect(getCurrentPosition).not.toHaveBeenCalled();
    }
  });
});

describe("10 설정의 권한 상태", () => {
  it("🚨 방금 거절한 뒤 다시 본 prompt 는 거절로 둔다 — 권한 창이 닫히며 다시 확인이 돈다", () => {
    expect(nextPermission("declined", "prompt")).toBe("declined");
    expect(nextPermission("declined", "granted")).toBe("granted");
    expect(nextPermission("declined", "denied")).toBe("denied");
    expect(nextPermission(null, "prompt")).toBe("prompt");
    expect(nextPermission("granted", "prompt")).toBe("prompt");
  });
});
