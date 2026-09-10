"use client";

import { useRouter } from "next/navigation";
import { useEffect, type ReactNode } from "react";

import { currentPath, rememberReturnPath } from "@/lib/auth";
import { hasLiveSession, useSessionStore } from "@/stores/session";

/**
 * 세션이 없으면 로그인 화면으로 되돌린다.
 *
 * 🚨 `hydrated` 전에는 아무것도 그리지 않는다. 토큰은 `sessionStorage` 에 있어서 서버 렌더
 *    시점에 항상 null 이다 — 그때 그리면 로그인한 사용자에게도 화면이 한 번 깜빡이고 튕긴다.
 *
 * 튕겨 나가기 전에 보고 있던 경로를 남긴다. 로그인이 끝나면 콜백 화면이 그 경로로 되돌린다
 * (docs/web/kakao-login-v1.md §4-2 — 복귀 화면 복원은 서버가 아니라 프론트 책임이다).
 *
 * 🚨 `/auth/callback` 은 이걸로 감싸지 않는다 — 토큰을 얻으러 가는 화면이다.
 * 지금은 온보딩 2화면에만 씌워 뒀다. 03~10 을 붙일 때 `child-scope.tsx` 위로 올릴지 정한다.
 */
export function AuthGate({ children }: { children: ReactNode }) {
  const router = useRouter();
  const hydrated = useSessionStore((s) => s.hydrated);
  const live = useSessionStore(hasLiveSession);

  useEffect(() => {
    if (!hydrated || live) return;
    rememberReturnPath(currentPath());
    router.replace("/");
  }, [hydrated, live, router]);

  if (!hydrated || !live) return null;
  return <>{children}</>;
}
