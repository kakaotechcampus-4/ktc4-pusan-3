import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/api/errors";
import { qk } from "@/lib/api/queryKeys";
import { getQueryClient } from "@/lib/query-client";

import { conversationOf, sendLine, useConversationStore } from "./conversation";

/**
 * 04 대화의 한 줄 보내기는 쿼리 캐시 밖이라, 보는 중에 아이 연결이 끊긴 403 에서 `/me` 를 직접 다시
 * 받게 해야 한다 (#802). 목은 아이 연결을 확인하지 않아(`consent` 시나리오가 아이 0명으로 동의를 묻는다)
 * 보내기만 403 으로 바꾼다.
 */
vi.mock("@/lib/api/operations", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api/operations")>()),
  submitInput: vi.fn(),
}));

// 테스트 환경은 `node` 라 `getQueryClient()` 가 부를 때마다 새로 만든다 (서버 쪽 동작). 브라우저처럼 하나로 둔다.
vi.mock("@/lib/query-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/query-client")>();
  const client = actual.makeQueryClient();
  return { ...actual, getQueryClient: () => client };
});

const { submitInput } = await import("@/lib/api/operations");

const CHILD = "c1";

beforeEach(() => {
  useConversationStore.getState().clearAll();
});

afterEach(() => {
  vi.restoreAllMocks();
});

async function failedSend(error: ApiError): Promise<void> {
  vi.mocked(submitInput).mockRejectedValueOnce(error);
  sendLine(CHILD, "지어낸 한 줄", { asReply: false, source: "home_input" });
  await vi.waitFor(() => {
    const [turn] = conversationOf(useConversationStore.getState(), CHILD).turns;
    expect(turn?.send.status).toBe("error");
  });
}

describe("한 줄 보내기가 실패하면", () => {
  it("🚨 child_access_denied 에서 /me 를 다시 받게 한다", async () => {
    const invalidate = vi.spyOn(getQueryClient(), "invalidateQueries");

    await failedSend(new ApiError(403, "child_access_denied", "x"));

    expect(invalidate).toHaveBeenCalledWith({ queryKey: qk.me(), exact: true });
  });

  it("다른 거절에서는 /me 를 건드리지 않는다", async () => {
    const invalidate = vi.spyOn(getQueryClient(), "invalidateQueries");

    await failedSend(new ApiError(422, "validation_failed", "x"));

    expect(invalidate).not.toHaveBeenCalledWith({ queryKey: qk.me(), exact: true });
  });
});
