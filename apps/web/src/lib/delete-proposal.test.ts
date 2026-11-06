import { describe, expect, it } from "vitest";

import { ApiError, NetworkError } from "@/lib/api/errors";

import { closedBy, stuckBy } from "./delete-proposal";

/**
 * 04 삭제 카드의 실패 갈래 (#535 · `docs/web/chat-run-v1.md` §5). 화면 문구가 단정해도 되는지가 여기서 갈린다.
 */

describe("closedBy — 409 는 실패가 아니라 닫힌 카드다", () => {
  it("만료와 이미 끝난 제안은 요약으로 접는다", () => {
    expect(closedBy(new ApiError(409, "proposal_expired", "x"))).toEqual({
      status: "expired",
      result: null,
    });
    expect(closedBy(new ApiError(409, "proposal_closed", "x"))).toEqual({
      status: "closed",
      result: null,
    });
  });

  it("다른 409 · 다른 상태는 접지 않는다", () => {
    expect(closedBy(new ApiError(409, "idempotency_in_progress", "x"))).toBeNull();
    expect(closedBy(new ApiError(404, "not_found", "x"))).toBeNull();
  });
});

describe("stuckBy — 다시 눌러도 같은 응답이면 버튼을 잠근다", () => {
  it("403 · 404 · 422 는 서버가 지우기 전에 낸다 — 이유를 말하고 잠근다", () => {
    // 🚨 403 은 단정하지 않는다 — 앞선 확정이 응답만 잃었으면 이미 지웠을 수 있다.
    expect(stuckBy(new ApiError(403, "child_access_denied", "x"))).toBe(
      "이 아이의 기록을 지금은 지울 수 없어요.",
    );
    expect(stuckBy(new ApiError(404, "not_found", "x"))).toMatch(/아무것도 지우지 않았어요/);
    expect(stuckBy(new ApiError(422, "not_in_proposal", "x"))).toMatch(/아무것도 지우지 않았어요/);
  });

  it("같은 상태 코드라도 다른 코드 이름이면 잠그지 않는다", () => {
    expect(stuckBy(new ApiError(422, "validation_failed", "x"))).toBeNull();
    expect(stuckBy(new ApiError(403, "consent_required", "x"))).toBeNull();
  });

  it("🚨 연결 실패 · 5xx 는 잠그지 않는다 — 서버가 지웠을 수 있어 같은 키로 다시 누르게 둔다", () => {
    expect(stuckBy(new ApiError(500, "internal", "x"))).toBeNull();
    expect(stuckBy(new NetworkError(new TypeError("fetch")))).toBeNull();
    expect(stuckBy(new Error("x"))).toBeNull();
  });
});
