"use client";

import { useRouter } from "next/navigation";
import { useEffect, type ReactNode } from "react";

import { SessionUnreachable } from "@/components/session-unreachable";
import { currentPath, rememberReturnPath } from "@/lib/auth";
import { hasLiveSession, useSessionStore } from "@/stores/session";

/**
 * 세션이 없으면 로그인 화면으로 되돌린다.
 *
 * 🚨 `hydrated` 전에는 아무것도 그리지 않는다. 앱을 켜면 토큰은 비어 있고 refresh 쿠키로
 *    되살리는 중이다 (`stores/session.ts`) — 그때 그리면 로그인한 사용자에게도 화면이 한 번
 *    깜빡이고 튕긴다.
 * 🚨 세션을 **확인하지 못한 것**(서버에 닿지 못함)은 로그아웃이 아니다. 튕기지 않고 연결
 *    확인 화면을 세운다 (`SessionUnreachable`).
 *
 * 튕겨 나가기 전에 보고 있던 경로를 남긴다. 로그인이 끝나면 콜백 화면이 그 경로로 되돌린다
 * (docs/web/kakao-login-v1.md §4-2 — 복귀 화면 복원은 서버가 아니라 프론트 책임이다).
 *
 * 🚨 `/auth/callback` 은 이걸로 감싸지 않는다 — 토큰을 얻으러 가는 화면이다.
 */
export function AuthGate({ children }: { children: ReactNode }) {
  const router = useRouter();
  const hydrated = useSessionStore((s) => s.hydrated);
  const live = useSessionStore(hasLiveSession);
  const restoreFailed = useSessionStore((s) => s.restoreFailed);

  useEffect(() => {
    if (!hydrated || live || restoreFailed) return;
    rememberReturnPath(currentPath());
    router.replace("/");
  }, [hydrated, live, restoreFailed, router]);

  if (!hydrated) return null;
  if (!live) return restoreFailed ? <SessionUnreachable /> : null;
  return <>{children}</>;
}
