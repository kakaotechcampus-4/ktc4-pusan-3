"use client";

import { useQuery } from "@tanstack/react-query";

import { api, qk, type Policy } from "@/lib/api";

/**
 * `GET /policies` — 동의 화면 셋(가입 동의 · 01 아이 만들기 · 10 설정)이 같이 쓴다.
 *
 * 🚨 **로그인 전에도 부른다.** 동의 화면은 계정이 만들어지기 전에 뜨고, 이 엔드포인트는
 *    무인증이다 (최상위 CLAUDE.md §9). `AuthGate` 안쪽에서 부르는 화면도 같은 쿼리를 쓴다 —
 *    캐시가 한 벌이라 아이 만들기 화면이 다시 기다리지 않는다.
 *
 * 🚨 **실패하면 동의를 받지 않는다.** 화면이 기본값으로 그리면 서버에 없는 버전으로
 *    동의를 보내게 되고, 그건 400 으로 돌아오거나 더 나쁘게는 **틀린 글에 동의한 기록**이
 *    된다. 화면은 오류를 보여주고 다시 불러오기만 준다.
 */
export function usePolicies() {
  return useQuery({
    queryKey: qk.policies(),
    queryFn: () => api.get<Policy[]>("/policies"),
    // 약관은 자주 바뀌지 않는다. 바뀐 것을 아는 순간(400 policy_version_invalid)에는
    // 화면이 직접 refetch 한다 — 그 경로가 정확해서 주기적으로 다시 받을 이유가 없다.
    staleTime: 5 * 60_000,
  });
}
