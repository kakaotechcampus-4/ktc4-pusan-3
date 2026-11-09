import type { Me } from "@/lib/api/types";

/**
 * 주소의 아이를 지금 보호자가 볼 수 있는가 (#802). 판정은 `components/child-scope.tsx` 한 곳에서 한다.
 *
 * - `allowed` — `/me` 의 아이 목록에 있다
 * - `denied` — 이번 화면에 들어온 뒤 새로 받은 목록에도 없다
 * - `stale` — 들어오기 전에 받아 둔 목록에 없다. 한 번 새로 받아 보고 정한다
 * - `checking` — 목록을 아직 못 받았다
 * - `unknown` — 목록을 받지 못했다. 볼 수 없다는 뜻이 아니다
 *
 * 🚨 **없는 아이, 남의 아이, 보관된 아이를 가르지 않는다.** 서버가 셋을 같은 403 으로 막는다
 *    (`apps/api/app/api/deps/child.py` — 가르면 그 id 의 아이가 있는지가 드러난다). 화면도 "내 목록에
 *    없다" 하나로만 본다. UUID 모양이 아닌 주소(서버는 422)도 여기서 같이 걸린다.
 * 🚨 **낡은 목록만 보고 막지 않는다.** 01 에서 막 만든 아이, 막 수락한 초대는 받아 둔 `/me` 에 아직
 *    없을 수 있다 (`stale`).
 * 🚨 **확인하지 못한 것은 막지 않는다** (`unknown`). 세션을 확인하지 못한 것을 로그아웃으로 보지 않는
 *    것과 같은 원칙이다 (`SessionUnreachable`). 화면을 그대로 그리고 실패는 구역마다 둔다.
 */
export type ChildAccess = "allowed" | "denied" | "stale" | "checking" | "unknown";

export function judgeChildAccess({
  childId,
  me,
  failed,
  fetchedAfterEnter,
}: {
  childId: string;
  /** 받아 둔 `/me`. 마지막 요청이 실패했어도 앞서 받은 값이 남아 있을 수 있다. */
  me: Me | undefined;
  /** 마지막 `/me` 요청이 실패했는가. */
  failed: boolean;
  /** 이 아이 화면에 들어온 뒤 `/me` 를 받은 적이 있는가 (성공이든 실패든). */
  fetchedAfterEnter: boolean;
}): ChildAccess {
  // 앞서 확인된 아이는 지금 요청이 실패해도 그대로 둔다 — 실패 하나로 화면을 거두지 않는다.
  if (me?.children.some((c) => c.child_id === childId)) return "allowed";
  if (failed) return "unknown";
  if (!me) return "checking";
  return fetchedAfterEnter ? "denied" : "stale";
}
