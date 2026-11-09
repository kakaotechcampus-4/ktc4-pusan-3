"use client";

import { useQuery } from "@tanstack/react-query";
import { useEffect, type ReactNode } from "react";

import { ChildUnavailable } from "@/components/child-unavailable";
import { SessionChecking } from "@/components/session-checking";
import { api, qk, type Me } from "@/lib/api";
import { judgeChildAccess } from "@/lib/child-access";
import { hasLiveSession, useSessionStore } from "@/stores/session";

/**
 * 아이 스코프 셸. 주소의 아이를 볼 수 있는지 여기서 한 번 판정한다 (#802).
 *
 * 🚨 화면이 읽는 childId 의 정본은 **URL** 이다. 스토어의 activeChildId 는
 *    "마지막에 본 아이" 복원용일 뿐이고, 여기서 URL → 스토어 방향으로만 흐른다.
 *    반대로 스토어를 읽어 화면을 그리면 뒤로가기와 딥링크가 어긋난다.
 *
 * 🚨 **`/me` 목록에서 확인된 뒤에만 화면을 그리고 스토어에 넣는다.** 확인 전에 그리면 볼 수 없는 id 로
 *    쿼리가 나가 구역마다 실패 카드가 서고, 그 id 가 "마지막에 본 아이" 로 남는다. 판정 규칙(낡은 목록,
 *    확인하지 못한 경우)은 `lib/child-access.ts` 에 있다.
 * 🚨 **세션이 없으면 판정하지 않고 넘긴다.** 이 셸은 페이지의 `AuthGate` 바깥이라, 여기서 막으면
 *    `AuthGate` 가 복귀 경로를 남기고 로그인으로 보내는 길을 가로챈다.
 *
 * 보는 중에 연결이 끊기면(다른 보호자가 연결을 끊음 · 보관) 쿼리가 `child_access_denied` 를 받고,
 * `lib/query-client.ts` 가 `/me` 를 다시 받게 한다 — 그러면 여기 판정이 다시 돈다.
 */
export function ChildScope({ childId, children }: { childId: string; children: ReactNode }) {
  const setActiveChild = useSessionStore((s) => s.setActiveChild);
  const live = useSessionStore(hasLiveSession);

  const me = useQuery({
    queryKey: qk.me(),
    queryFn: () => api.get<Me>("/me"),
    enabled: live,
  });
  const access = judgeChildAccess({
    childId,
    me: me.data,
    failed: me.isError,
    fetchedAfterEnter: me.isFetchedAfterMount,
  });

  // 들어오기 전에 받아 둔 목록에 없으면 한 번 새로 받아 본다. 받은 뒤에는 `stale` 이 다시 오지 않는다.
  const { isFetching, refetch } = me;
  useEffect(() => {
    if (live && access === "stale" && !isFetching) void refetch();
  }, [live, access, isFetching, refetch]);

  useEffect(() => {
    if (access === "allowed") setActiveChild(childId);
  }, [access, childId, setActiveChild]);

  if (!live) return <>{children}</>;
  if (access === "allowed" || access === "unknown") return <>{children}</>;
  if (access === "denied" && me.data) return <ChildUnavailable me={me.data} />;
  return <SessionChecking />;
}
