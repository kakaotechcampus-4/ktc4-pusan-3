import type { Relation } from "@/lib/api/types";

/**
 * 아이와의 관계 정본. 🚨 **라벨을 화면마다 다시 쓰지 않는다** — 같은 값이 10 설정에서는
 * "엄마", 등록 화면에서는 "어머니" 로 보이면 같은 사람이 두 사람으로 읽힌다.
 *
 * 🚨 **고를 수 있는 목록이 자리마다 다르다** (아래 두 상수). 이건 편의가 아니라
 *    **법정대리인 동의를 누가 하는가**의 문제다.
 */
export const RELATION_LABEL: Record<Relation, string> = {
  mother: "엄마",
  father: "아빠",
  grandparent: "조부모",
  // ⚠️ 두 벌이 **서로 달랐다** — 10 설정은 "돌봄 선생님", 01 등록 화면은 "시터" 였다.
  //    같은 값이 화면마다 다른 이름으로 보이던 것이라, 부모에게 이미 보이고 있던
  //    10 설정 쪽을 정본으로 골랐다. 이게 한 곳에 모인 이유다.
  sitter: "돌봄 선생님",
  other: "그 밖에",
};

/**
 * 01 아이를 **등록하는** 보호자가 고를 수 있는 것.
 *
 * 🚨 **시터와 "그 밖에" 를 빼 둔다.** 이 화면에서 같이 받는 것이 아이 정보에 대한
 *    **법정대리인 동의**(개인정보보호법 제22조의2)이고, 시터는 법정대리인이 아니다.
 *    조부모는 후견인일 수 있어 남긴다. 목록에 세워 두면 **법정대리인이 아닌 사람에게서
 *    법정대리인 동의를 받는 경로**가 화면에 생긴다 — 그 동의는 무효다.
 *    아이를 함께 보는 시터는 초대로 들어온다 (#96).
 */
export const OWNER_RELATIONS: Relation[] = ["mother", "father", "grandparent"];

/**
 * 초대로 **들어오는** 보호자가 고를 수 있는 것. 5종 전부다.
 *
 * 여기서 넓히는 이유는 그 반대다 — 이 사람은 법정대리인 동의를 하지 않는다(아이를 등록한
 * 보호자가 이미 했다). 그래서 시터·그 밖에도 자기 관계를 그대로 말할 수 있다.
 *
 * 🚨 **관계는 받는 쪽이 고른다.** 초대를 발행할 때 지정하지 않는다 (#89) — 잘못 찍으면
 *    받는 쪽이 자기 프로필을 고치러 가야 하고, 그 값은 기록마다 "누가 적었나" 로 남는다.
 */
export const MEMBER_RELATIONS: Relation[] = ["mother", "father", "grandparent", "sitter", "other"];

/** 칩·상자에 넘길 모양으로. 화면이 라벨을 다시 쓰지 않게 한다. */
export function relationOptions(relations: Relation[]): Array<{ value: Relation; label: string }> {
  return relations.map((value) => ({ value, label: RELATION_LABEL[value] }));
}
