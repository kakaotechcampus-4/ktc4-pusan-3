import { MutationCache, QueryCache, QueryClient, isServer } from "@tanstack/react-query";

import { ApiError, isApiError } from "@/lib/api/errors";
import { qk } from "@/lib/api/queryKeys";

/**
 * 🚨 **보는 중에 아이 연결이 끊기면 `/me` 만 다시 받는다** (#802). 다른 보호자가 연결을 끊었거나
 *    아이가 보관되면 그 아이의 요청이 403 `child_access_denied` 를 받는다. 화면마다 다루지 않고,
 *    `/me` 를 다시 받으면 `ChildScope` 의 판정이 다시 돌아 화면째 "볼 수 없는 아이" 로 바뀐다.
 *    화면을 직접 바꾸지 않는다 — 판정은 `ChildScope` 한 곳이다 (`throwOnError` 도 쓰지 않는다).
 *    `/me` 는 이 코드를 내지 않아서 다시 받는 것이 또 다시 받기를 부르지 않는다.
 *
 * 쿼리 · 뮤테이션은 아래 캐시가 부른다. 그 밖에서 아이 주소를 부르는 곳(04 대화의 한 줄 보내기 ·
 * `stores/conversation.ts`)은 실패에서 이 함수를 직접 부른다.
 */
export function refreshMeOnLostChild(client: QueryClient, error: unknown): void {
  if (isApiError(error, "child_access_denied")) {
    void client.invalidateQueries({ queryKey: qk.me(), exact: true });
  }
}

/** 앱은 `getQueryClient()` 하나를 쓴다. 이 함수를 따로 부르는 곳은 테스트뿐이다. */
export function makeQueryClient() {
  const onError = (error: unknown) => refreshMeOnLostChild(client, error);

  const client: QueryClient = new QueryClient({
    queryCache: new QueryCache({ onError }),
    mutationCache: new MutationCache({ onError }),
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
  return client;
}

let browserQueryClient: QueryClient | undefined;

export function getQueryClient(): QueryClient {
  if (isServer) return makeQueryClient();
  browserQueryClient ??= makeQueryClient();
  return browserQueryClient;
}
