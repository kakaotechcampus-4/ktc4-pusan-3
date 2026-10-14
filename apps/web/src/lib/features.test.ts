import { afterEach, describe, expect, it, vi } from "vitest";

/**
 * 실서버에 없는 기능의 진입점이 **목이 뜨는 환경에서만** 열리는지 (#409).
 *
 * 화면 쪽은 e2e 가 목을 켜고 돌아서 열린 상태만 지나간다. 닫힌 상태를 거는 것은 여기다 —
 * 조건이 느슨해지면(목 스위치 하나만 보거나 `!== "production"` 으로 바뀌면) 실서버 빌드에
 * 404 로 끝나는 버튼이 다시 선다.
 */
async function loadFeatures(nodeEnv: string, mocking: string) {
  vi.stubEnv("NODE_ENV", nodeEnv);
  vi.stubEnv("NEXT_PUBLIC_API_MOCKING", mocking);
  vi.resetModules();
  const { PENDING_SERVER_FEATURES } = await import("@/lib/features");
  return PENDING_SERVER_FEATURES;
}

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("PENDING_SERVER_FEATURES", () => {
  it("개발 + 목 켬이면 진입점이 열린다", async () => {
    expect(await loadFeatures("development", "enabled")).toEqual({
      photoEntry: true,
      safetyScan: true,
    });
  });

  it("목을 끈 개발 서버(실서버에 붙는다)에서는 닫힌다", async () => {
    expect(await loadFeatures("development", "disabled")).toEqual({
      photoEntry: false,
      safetyScan: false,
    });
  });

  it("프로덕션 빌드는 목 스위치가 켜져 있어도 닫힌다 — 목이 뜨지 않는다", async () => {
    expect(await loadFeatures("production", "enabled")).toEqual({
      photoEntry: false,
      safetyScan: false,
    });
  });

  it("테스트 환경처럼 개발이 아닌 곳에서도 닫힌다", async () => {
    expect(await loadFeatures("test", "enabled")).toEqual({
      photoEntry: false,
      safetyScan: false,
    });
  });
});
