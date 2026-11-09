import { describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/api/errors";
import { qk } from "@/lib/api/queryKeys";

import { makeQueryClient } from "./query-client";

/**
 * 보는 중에 아이 연결이 끊기면 `/me` 만 다시 받는다 (#802). 화면을 바꾸는 것은 `ChildScope` 의 판정이고,
 * 여기서는 그 판정이 다시 돌 계기를 만드는지만 본다.
 */
describe("child_access_denied 를 받으면", () => {
  const lost = new ApiError(403, "child_access_denied", "x");

  it("🚨 쿼리 실패에서 /me 를 다시 받게 한다", async () => {
    const client = makeQueryClient();
    const invalidate = vi.spyOn(client, "invalidateQueries");

    await expect(
      client.fetchQuery({ queryKey: ["child", "c1", "home"], queryFn: () => Promise.reject(lost) }),
    ).rejects.toBe(lost);

    expect(invalidate).toHaveBeenCalledWith({ queryKey: qk.me(), exact: true });
  });

  it("🚨 뮤테이션 실패에서도 /me 를 다시 받게 한다", async () => {
    const client = makeQueryClient();
    const invalidate = vi.spyOn(client, "invalidateQueries");

    await expect(
      client
        .getMutationCache()
        .build(client, { mutationFn: () => Promise.reject(lost) })
        .execute(undefined),
    ).rejects.toBe(lost);

    expect(invalidate).toHaveBeenCalledWith({ queryKey: qk.me(), exact: true });
  });

  it("다른 403 에서는 /me 를 건드리지 않는다", async () => {
    const client = makeQueryClient();
    const invalidate = vi.spyOn(client, "invalidateQueries");
    const consent = new ApiError(403, "consent_required", "x");

    await expect(
      client.fetchQuery({
        queryKey: ["child", "c1", "home"],
        queryFn: () => Promise.reject(consent),
      }),
    ).rejects.toBe(consent);

    expect(invalidate).not.toHaveBeenCalled();
  });
});
