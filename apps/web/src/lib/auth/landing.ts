import type { Me } from "@/lib/api/types";

/**
 * 로그인한 보호자가 처음 설 화면.
 *
 * 마지막에 본 아이가 아직 내 아이 목록에 있으면 그 아이의 홈, 아니면 첫 아이의 홈이다.
 * 🚨 기억해 둔 id 를 그대로 주소로 쓰지 않는다 — 다른 계정이 같은 기기에서 남긴 값일 수 있다.
 *    `/me` 목록에 있는 것만 쓴다.
 * 🚨 아이가 없으면 01(아이 만들기)이 아니라 00-1 로 보낸다. 초대를 기다리는 사람이 바로 아이
 *    만들기에 떨어지면 **같은 아이를 또 등록**하고, 그 뒤로는 초대를 수락할 수 없다 (#96).
 */
export function landingPath(me: Me, lastChildId: string | null): string {
  const child = me.children.find((c) => c.child_id === lastChildId) ?? me.children[0];
  return child ? `/child/${child.child_id}/home` : "/start";
}
