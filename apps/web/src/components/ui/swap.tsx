"use client";

import type { ReactNode } from "react";

import { useEnterMotion, type EnterMotion } from "@/lib/motion";

/**
 * 같은 자리의 내용이 바뀔 때 새 내용이 투명도로 선다 (디자인 시스템 §8 "교체").
 * 스켈레톤 → 내용, 진행 표시 → 결과, 실패 카드 → 다시 받은 내용처럼 **데이터가 바꾸는** 자리에 쓴다.
 *
 * ```tsx
 * <Swap id={query.isPending ? "pending" : query.isError ? "error" : "ready"}>
 *   {query.isPending ? <SkeletonBlock /> : ...}
 * </Swap>
 * ```
 *
 * 🚨 **처음 그려질 때는 움직이지 않는다.** 캐시가 있어 스켈레톤 없이 바로 선 화면도 그대로 선다 —
 *    `id` 가 바뀐 순간에만 움직인다 (`useEnterMotion`).
 * 🚨 **레이아웃에 상자를 하나 더 끼우지 않는다** (`display: contents`). 부모의 `gap` · `flex` 가 안의
 *    요소에 그대로 걸리고, 내용이 비면 빈 칸도 생기지 않는다. 그래서 움직이는 것은 이 상자가 아니라
 *    **안의 요소들 하나하나**다.
 * 🚨 `id` 는 **무엇이 서 있는가**를 가르는 원시값이다. 내용이 바뀔 때마다 바뀌는 값(건수 같은)을 넣으면
 *    다시 받을 때마다 깜빡인다.
 */
export function Swap({
  id,
  motion = "fade",
  children,
}: {
  id: string;
  motion?: EnterMotion;
  children: ReactNode;
}) {
  const ref = useEnterMotion(id, motion, (root) => root.children);

  return (
    <div ref={ref} className="contents">
      {children}
    </div>
  );
}

/**
 * 쿼리가 지금 무엇을 세우는가 — `Swap` 의 `id` 로 쓴다.
 * 🚨 다시 받는 동안(`isFetching`)은 바뀌지 않는다 — 새로고침 · 무효화마다 깜빡이지 않게.
 * 🚨 **받아 둔 데이터가 있으면 실패해도 "ready" 다.** 다음 장만 실패했거나 다시 받기가 실패해도
 *    화면은 받아 둔 목록을 그대로 두고 옆에서 말한다 — 그 순간 목록 전체가 깜빡이면 안 된다.
 */
export function phaseOf(query: { isPending: boolean; data?: unknown }): string {
  return query.isPending ? "pending" : query.data !== undefined ? "ready" : "error";
}
