"use client";

import { useQuery } from "@tanstack/react-query";

import { api, qk, type ConsentsResponse } from "@/lib/api";

/**
 * `GET /consents?child_id=` — 지금 켜져 있는 동의와 이력. 10 설정의 동의 구역과 입력 보내기(위치)가 같이 쓴다.
 * 🚨 캐시가 한 벌이다 — 10 설정에서 동의를 바꾸면(`invalidateQueries`) 03 · 04 가 같은 값을 다시 받는다.
 */
export function useConsents(childId: string) {
  return useQuery({
    queryKey: qk.consents(childId),
    queryFn: () => api.get<ConsentsResponse>("/consents", { query: { child_id: childId } }),
  });
}

/**
 * 입력에 흐린 위치를 실을 것인가 — 위치 동의(`location`)가 지금 켜져 있을 때만 (#593).
 * 🚨 **모르면 싣지 않는다.** 불러오는 중이거나 못 불러왔으면 `false` 다.
 */
export function useLocationConsent(childId: string): boolean {
  return useConsents(childId).data?.effective.location === true;
}
