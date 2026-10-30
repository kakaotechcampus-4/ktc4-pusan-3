import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { claimLocationNudge, resetLocationNudge } from "./location-nudge";

/** 놀이 추천 아래 위치 안내 — 아무것도 안 누르면 하루에 한 번 (#638 리뷰). 저장소에는 날짜 하나만 둔다. */

function stubStorage() {
  const store = new Map<string, string>();
  vi.stubGlobal("localStorage", {
    getItem: (key: string) => store.get(key) ?? null,
    setItem: (key: string, value: string) => void store.set(key, value),
  });
  return store;
}

beforeEach(() => resetLocationNudge());
afterEach(() => vi.unstubAllGlobals());

describe("하루에 한 번", () => {
  it("그날 처음 줄에만 띄우고, 그 줄은 다시 그려도 계속 띄운다", () => {
    stubStorage();
    expect(claimLocationNudge("t1", "2026-10-30")).toBe(true);
    expect(claimLocationNudge("t2", "2026-10-30")).toBe(false);
    expect(claimLocationNudge("t1", "2026-10-30")).toBe(true);
  });

  it("다음 날에는 다시 띄운다", () => {
    stubStorage();
    expect(claimLocationNudge("t1", "2026-10-30")).toBe(true);
    expect(claimLocationNudge("t2", "2026-10-31")).toBe(true);
  });

  it("🚨 저장소에는 날짜 하나만 둔다", () => {
    const store = stubStorage();
    claimLocationNudge("t1", "2026-10-30");
    expect([...store.values()]).toEqual(["2026-10-30"]);
  });

  it("저장소를 못 쓰면 이 탭에서만 기억한다", () => {
    vi.stubGlobal("localStorage", {
      getItem: () => {
        throw new Error("blocked");
      },
      setItem: () => {
        throw new Error("blocked");
      },
    });
    expect(claimLocationNudge("t1", "2026-10-30")).toBe(true);
    expect(claimLocationNudge("t2", "2026-10-30")).toBe(false);
  });
});
