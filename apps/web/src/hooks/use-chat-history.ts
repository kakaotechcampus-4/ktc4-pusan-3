"use client";

import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { useMemo } from "react";

import { api, qk, type ChatDayResponse } from "@/lib/api";
import {
  chatQuery,
  olderPageParam,
  pastTurnsOf,
  type ChatPageParam,
  type PastTurn,
} from "@/lib/chat-history";

/**
 * 04 지난 대화 (#228). 첫 장은 서버의 오늘이고, `fetchNextPage` 가 더 이른 장(같은 날의 앞부분 → 전날)을 붙인다.
 *
 * 🚨 **서버 상태다** — 대화 스토어(`stores/conversation.ts`)에 옮겨 담지 않는다. 스토어는 이 앱에서 보낸 줄과
 *    그 run 을 들고 있고, 이건 서버가 남긴 것이다. 둘은 화면에서 `run_id` 로 합친다 (`ChatThread`).
 * 🚨 `throwOnError` 를 켜지 않는다 — 지난 대화를 못 받아도 지금 보낸 줄은 그려야 한다 (NF-06).
 */
export function useChatHistory(childId: string) {
  const query = useInfiniteQuery({
    queryKey: qk.chat(childId),
    queryFn: ({ pageParam }) =>
      api.get<ChatDayResponse>(`/children/${childId}/chat`, { query: chatQuery(pageParam) }),
    initialPageParam: null as ChatPageParam | null,
    getNextPageParam: olderPageParam,
  });
  const pages = query.data?.pages;
  const turns = useMemo<PastTurn[]>(() => (pages ? pastTurnsOf(pages) : []), [pages]);
  return { ...query, turns };
}

/**
 * 03 홈 — 서버에 오늘 대화가 있는가. 🚨 앱을 다시 켜면 대화 스토어는 비어 있어서, 이걸 안 보면
 * "오늘 대화 이어보기" 가 사라진다. 첫 장만 본다 (`qk.chatToday` 의 🚨).
 */
export function useHasTodayChat(childId: string): boolean {
  const today = useQuery({
    queryKey: qk.chatToday(childId),
    queryFn: () => api.get<ChatDayResponse>(`/children/${childId}/chat`),
  });
  return (today.data?.turns.length ?? 0) > 0;
}
