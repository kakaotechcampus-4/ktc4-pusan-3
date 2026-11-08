import { ApiError } from "@/lib/api/errors";
import type { DeleteProposalStatus } from "@/lib/api/types";

/**
 * 04 삭제 카드의 확정 · 취소가 실패했을 때 화면이 어디로 가는가 (#535 · `components/record-tabs-card.tsx`).
 * 규칙 원문은 `docs/web/chat-run-v1.md` §5.
 */

/**
 * 버튼을 누른 뒤의 끝. 서버 상태에 `closed` 하나가 더 있다 — 409 `proposal_closed` 는 지웠는지 취소했는지를
 * 싣지 않아서 화면이 둘 중 하나로 말할 수 없다.
 */
export type Settled = {
  status: Exclude<DeleteProposalStatus, "pending"> | "closed";
  result: Record<string, number> | null;
};

/** 409 두 가지는 실패가 아니라 "이미 닫힌 카드" 다 — 같은 자리에서 요약으로 접는다. */
export function closedBy(error: unknown): Settled | null {
  if (!(error instanceof ApiError) || error.status !== 409) return null;
  if (error.code === "proposal_expired") return { status: "expired", result: null };
  if (error.code === "proposal_closed") return { status: "closed", result: null };
  return null;
}

/**
 * 🚨 **다시 눌러도 같은 응답이 오는 실패** — 버튼을 잠그고 이유를 말한다 (`docs/web/api-client-v1.md` §3).
 *    셋 다 서버가 **지우기 전에** 내는 응답이다. 404 · 422 는 지운 뒤에 올 수 없어서(그때는 409 `proposal_closed`)
 *    "아무것도 지우지 않았어요" 라고 말해도 된다. 403 만 예외다 (아래).
 *    연결 실패 · 5xx 는 여기 오지 않는다 — 서버가 지웠을 수 있어 같은 키로 다시 누르게 둔다.
 * @returns 화면 문구. 다시 눌러 볼 만한 실패면 `null`.
 */
export function stuckBy(error: unknown): string | null {
  if (!(error instanceof ApiError)) return null;
  // 🚨 상태 코드가 아니라 코드 이름으로 고른다 — 서버가 같은 상태로 "다시 해 볼 만한" 오류를 더해도 잠그지 않게.
  switch (error.code) {
    // ⚠️ 단정하지 않는다 — 앞선 확정이 응답만 잃은 채 서버에서 지웠고 그 사이 연결이 끊겼으면, 같은 키의 재생보다
    //    아이 확인(403)이 먼저라 지운 것이 맞는데도 이 응답이 온다. 드물지만 거짓 문구가 된다.
    case "child_access_denied":
      return "이 아이의 기록을 지금은 지울 수 없어요.";
    case "not_found":
      return "이 삭제 확인을 찾지 못했어요. 아무것도 지우지 않았어요.";
    case "not_in_proposal":
      return "확인에 없던 것이 섞여 있어서 아무것도 지우지 않았어요. 다시 말씀해 주세요.";
    default:
      return null;
  }
}
