import type { Me } from "@/lib/api/types";

import { me } from "../fixtures";
import { currentScenario } from "../scenario";

/**
 * 이 세션에서 **연결된 아이**. 목이 상태를 들고 있어야 하는 자리 중 하나다.
 *
 * 🚨 **신규 가입은 아이 0명에서 시작한다.** `GET /me` 가 늘 아이 하나를 돌려주면
 *    00-1 경로 고르기 화면과 초대 코드 화면을 **아예 열어 볼 수 없다** — 두 화면 다
 *    "아이가 없는 계정" 에서만 서고, 있으면 홈으로 되돌리기 때문이다.
 *
 * 그래서 `consent`(신규 가입) 시나리오에서만 이 목록이 정본이고, 여기에 아이가 들어오는
 * 길은 실서버와 같은 둘뿐이다 — `POST /children` 과 `POST /invites/{code}/accept`.
 * 다른 시나리오는 "이미 쓰고 있는 계정" 이라 `me` 픽스처를 그대로 쓴다.
 */
let joined: Me["children"] = [];

export function resetMembership(): void {
  joined = [];
}

export function joinChild(child: Me["children"][number]): void {
  joined = [...joined.filter((c) => c.child_id !== child.child_id), child];
}

/** `GET /me` 가 돌려줄 것. 🚨 손으로 만들지 말고 이걸 부른다 — 두 곳이 어긋나면 화면이 튕긴다. */
export function currentMe(): Me {
  if (currentScenario() !== "consent") return me;
  return { ...me, children: joined };
}
