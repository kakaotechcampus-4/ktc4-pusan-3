"use client";

import { QueryClientProvider } from "@tanstack/react-query";
import { ReactQueryDevtools } from "@tanstack/react-query-devtools";
import { useEffect, type ReactNode } from "react";

import { getQueryClient } from "@/lib/query-client";
import { useSessionStore } from "@/stores/session";

export function Providers({ children }: { children: ReactNode }) {
  const queryClient = getQueryClient();

  // localStorage 는 서버에 없다. 마운트 후에 복구해야 하이드레이션 불일치가 안 난다.
  useEffect(() => {
    void useSessionStore.persist.rehydrate();
  }, []);

  return (
    <QueryClientProvider client={queryClient}>
      {children}
      {/* devtools 는 프로덕션 번들에서 빠진다. 쿼리 키·캐시 상태를 눈으로 볼 때 쓴다. */}
      {process.env.NODE_ENV === "development" ? <ReactQueryDevtools initialIsOpen={false} /> : null}
    </QueryClientProvider>
  );
}
