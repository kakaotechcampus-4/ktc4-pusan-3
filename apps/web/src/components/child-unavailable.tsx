"use client";

import { ErrorView } from "@/components/error-view";
import { ButtonLink } from "@/components/ui/button";
import type { Me } from "@/lib/api/types";
import { landingPath } from "@/lib/auth/landing";
import { useSessionStore } from "@/stores/session";

/**
 * 주소의 아이가 내 아이 목록에 없을 때의 화면 (#802 · `lib/child-access.ts`).
 *
 * 🚨 **아이가 "있다" 를 전제하는 말을 쓰지 않는다** ("다른 보호자의 아이" 같은). 서버가 없는 아이와
 *    남의 아이를 일부러 같은 403 으로 막는다 — 화면이 둘 중 하나로 말하면 그 구분이 새고, 없는 아이에게는
 *    틀린 말이다.
 * 🚨 **다른 아이 화면으로 저절로 보내지 않는다.** 바뀐 줄 모르고 다른 아이에게 기록을 적게 된다.
 *    가는 길은 버튼 하나고, 주소는 `/me` 목록에서만 만든다 (`landingPath` — URL 의 id 를 쓰지 않는다).
 *    마지막에 본 아이를 넘기는 것은 아이가 여럿일 때 보던 아이로 돌려보내려는 것이다. 막힌 id 는 스토어에
 *    들어가지 않고(`ChildScope`), 넘긴 값도 `landingPath` 가 목록으로 다시 거른다.
 * 초대 코드 입력으로 가는 길은 따로 세우지 않는다. 아이가 있는 보호자는 초대를 수락할 수 없고(#96),
 * 아이가 없으면 `landingPath` 가 그 길이 있는 `/start` 로 보낸다.
 */
export function ChildUnavailable({ me }: { me: Me }) {
  const lastChildId = useSessionStore((s) => s.activeChildId);
  const hasChild = me.children.length > 0;
  return (
    <ErrorView
      title="볼 수 없는 아이예요"
      description="주소가 잘못됐거나, 지금은 함께 보고 있지 않은 아이예요."
      action={
        <ButtonLink href={landingPath(me, lastChildId)} block>
          {hasChild ? "내 아이 홈으로" : "처음 화면으로"}
        </ButtonLink>
      }
    />
  );
}
