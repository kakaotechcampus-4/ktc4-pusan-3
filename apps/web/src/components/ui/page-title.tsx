import type { ReactNode } from "react";

import { cn } from "@/lib/cn";

/**
 * 화면 제목. 디자인 시스템 §4 의 `display` 다.
 *
 * 🚨 §9 가 **좁은 폰(< 380px)에서는 `display` 를 `title` 로 낮추라**고 정했다.
 *    화면마다 손으로 쓰면 어긋나므로 여기 한 곳에 둔다 — 24px 두 줄 제목은
 *    360px 기기에서 세 줄로 흐른다.
 */
export function PageTitle({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <h1 className={cn("text-title min-[380px]:text-display text-ink", className)}>{children}</h1>
  );
}
