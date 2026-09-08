import { QueryClient, isServer } from "@tanstack/react-query";

import { ApiError } from "@/lib/api/errors";

function makeQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: {
        // SSR 직후 클라이언트가 곧바로 다시 받아오는 것을 막는다.
        staleTime: 30_000,
        retry: (failureCount, error) => {
          // 401·403·404·409·422 는 다시 시도해도 같은 답이다.
          if (error instanceof ApiError && error.status < 500) return false;
          return failureCount < 2;
        },
        refetchOnWindowFocus: false,
      },
      mutations: {
        // 🚨 승인 게이트(캘린더 쓰기 · 건강 기록 확정)를 자동 재시도하지 않는다.
        //    되돌릴 수 없는 것을 두 번 실행할 수 있다 (CLAUDE.md §2).
        retry: false,
      },
    },
  });
}

let browserQueryClient: QueryClient | undefined;

export function getQueryClient(): QueryClient {
  if (isServer) return makeQueryClient();
  browserQueryClient ??= makeQueryClient();
  return browserQueryClient;
}
