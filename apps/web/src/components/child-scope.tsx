"use client";

import { useEffect, type ReactNode } from "react";

import { useSessionStore } from "@/stores/session";

/**
 * 아이 스코프 셸.
 *
 * 🚨 화면이 읽는 childId 의 정본은 **URL** 이다. 스토어의 activeChildId 는
 *    "마지막에 본 아이" 복원용일 뿐이고, 여기서 URL → 스토어 방향으로만 흐른다.
 *    반대로 스토어를 읽어 화면을 그리면 뒤로가기와 딥링크가 어긋난다.
 */
export function ChildScope({ childId, children }: { childId: string; children: ReactNode }) {
  const setActiveChild = useSessionStore((s) => s.setActiveChild);

  useEffect(() => {
    setActiveChild(childId);
  }, [childId, setActiveChild]);

  return <>{children}</>;
}
