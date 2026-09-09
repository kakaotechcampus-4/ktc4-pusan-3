"use client";

import { QueryClientProvider } from "@tanstack/react-query";
import { ReactQueryDevtools } from "@tanstack/react-query-devtools";
import { useRouter } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";

import { setUnauthenticatedHandler } from "@/lib/api/client";
import { currentPath, rememberReturnPath } from "@/lib/auth";
import { getQueryClient } from "@/lib/query-client";
import { startMocks } from "@/mocks/start";
import { useSessionStore } from "@/stores/session";

/** 목을 켜지 않는 환경에서는 startMocks 가 즉시 끝나므로 화면이 지연되지 않는다. */
const MOCKING = process.env.NEXT_PUBLIC_API_MOCKING === "enabled";

export function Providers({ children }: { children: ReactNode }) {
  const queryClient = getQueryClient();
  const router = useRouter();

  // 워커가 뜨기 전에 나간 요청은 목을 통과해 실서버로 간다. 그래서 기다렸다가 그린다.
  const [mocksReady, setMocksReady] = useState(!MOCKING);

  useEffect(() => {
    if (!MOCKING) return;
    void startMocks().finally(() => setMocksReady(true));
  }, []);

  // sessionStorage 는 서버에 없다. 마운트 후에 복구해야 하이드레이션 불일치가 안 난다.
  useEffect(() => {
    void useSessionStore.persist.rehydrate();
  }, []);

  /**
   * 세션 만료(401 unauthenticated)를 한 곳에서 처리한다 — 화면마다 다루면 어딘가는 빠뜨린다.
   * client.ts 가 스토어를 직접 import 하지 않기 때문에(서버 컴포넌트에서 못 쓰게 된다)
   * 여기서 핸들러를 꽂아 준다.
   */
  useEffect(() => {
    setUnauthenticatedHandler(() => {
      // 만료로 튕기는 것이라 로그인 후 되돌아올 화면을 남긴다.
      rememberReturnPath(currentPath());
      useSessionStore.getState().signOut();
      queryClient.clear();
      router.replace("/");
    });
    return () => setUnauthenticatedHandler(null);
  }, [router, queryClient]);

  return (
    <QueryClientProvider client={queryClient}>
      {mocksReady ? children : null}
      {/* devtools 는 프로덕션 번들에서 빠진다. 쿼리 키·캐시 상태를 눈으로 볼 때 쓴다. */}
      {process.env.NODE_ENV === "development" ? <ReactQueryDevtools initialIsOpen={false} /> : null}
    </QueryClientProvider>
  );
}
