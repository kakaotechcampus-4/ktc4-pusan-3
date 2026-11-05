import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { notifyFirstScreen, resetFirstScreenForTest } from "./splash";

/**
 * 스플래시의 웹 쪽 약속 (#690). 셸이 받는 쪽은 `apps/mobile/src/native/splash.test.ts` 가 건다.
 */

beforeEach(() => {
  vi.stubGlobal("window", new EventTarget());
  resetFirstScreenForTest();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("notifyFirstScreen", () => {
  it("셸이 없으면(브라우저) 아무것도 하지 않고 던지지 않는다", () => {
    expect(() => notifyFirstScreen()).not.toThrow();
  });

  it("셸에 첫 화면을 알린다", () => {
    const done = vi.fn();
    window.icatch = { splash: { done } };
    notifyFirstScreen();
    expect(done).toHaveBeenCalledTimes(1);
  });

  it("🚨 화면을 옮겨 Screen 이 다시 서도 한 문서에서 한 번만 알린다", () => {
    const done = vi.fn();
    window.icatch = { splash: { done } };
    notifyFirstScreen();
    notifyFirstScreen();
    expect(done).toHaveBeenCalledTimes(1);
  });

  it("창구가 늦게 꽂혀도 그때 처음 서는 화면이 알린다", () => {
    notifyFirstScreen();
    const done = vi.fn();
    window.icatch = { splash: { done } };
    notifyFirstScreen();
    expect(done).toHaveBeenCalledTimes(1);
  });
});
