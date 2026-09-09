"use client";

import { useRouter } from "next/navigation";
import { useEffect, type ReactNode } from "react";

import { useSessionStore } from "@/stores/session";

/**
 * 토큰이 없으면 로그인 화면으로 되돌린다.
 *
 * 🚨 `hydrated` 전에는 아무것도 그리지 않는다. 토큰은 localStorage 에 있어서 서버 렌더 시점에
 *    항상 null 이다 — 그때 그리면 로그인한 사용자에게도 화면이 한 번 깜빡이고 튕긴다.
 *
 * 지금은 온보딩 2화면에만 씌워 뒀다. 03~10 을 붙일 때 `child-scope.tsx` 위로 올릴지 정한다.
 */
export function AuthGate({ children }: { children: ReactNode }) {
  const router = useRouter();
  const hydrated = useSessionStore((s) => s.hydrated);
  const token = useSessionStore((s) => s.token);

  useEffect(() => {
    if (hydrated && !token) router.replace("/");
  }, [hydrated, token, router]);

  if (!hydrated || !token) return null;
  return <>{children}</>;
}
