"use client";

import { QueryClientProvider } from "@tanstack/react-query";
import { ReactQueryDevtools } from "@tanstack/react-query-devtools";
import { useRouter } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";

import { ToastProvider } from "@/components/ui/toast";
import { WebPushBridge } from "@/components/web-push-bridge";
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

  /**
   * 앱을 켤 때 세션을 되살린다 — 토큰은 메모리에만 있어서 늘 비어 있고, refresh 쿠키로 다시 받는다
   * (`stores/session.ts`). 저장소와 쿠키는 서버 렌더에 없으니 마운트 후에 해야 하이드레이션이
   * 어긋나지 않는다.
   *
   * 🚨 **목이 뜬 뒤에 한다.** 워커보다 먼저 나간 갱신은 목을 통과해 실서버로 간다.
   */
  useEffect(() => {
    if (!mocksReady) return;
    void useSessionStore.persist.rehydrate();
    void useSessionStore.getState().restore();
  }, [mocksReady]);

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
      {/* 🚨 토스트는 **쿼리 안쪽**이다 — 뮤테이션 `onError` 에서 부르기 때문이다.
          바깥에 두면 화면이 Provider 밖에서 `useToast()` 를 부르게 된다. */}
      <ToastProvider>
        {mocksReady ? (
          <>
            {children}
            {/* 🚨 목이 뜬 뒤에 선다 — 기기 등록 요청이 목을 지나쳐 실서버로 가지 않게 (#609). */}
            <WebPushBridge />
          </>
        ) : null}
      </ToastProvider>
      {/* devtools 는 프로덕션 번들에서 빠진다. 쿼리 키·캐시 상태를 눈으로 볼 때 쓴다.
          🚨 왼쪽 아래로 옮겨 뒀다 — 기본값(오른쪽 아래)이 03 홈 채팅바의 **보내기 버튼을 덮어서**
             개발 중에 누르면 devtools 가 열린다 (실제로 클릭이 막혔다). */}
      {process.env.NODE_ENV === "development" ? (
        <ReactQueryDevtools initialIsOpen={false} buttonPosition="bottom-left" />
      ) : null}
    </QueryClientProvider>
  );
}
