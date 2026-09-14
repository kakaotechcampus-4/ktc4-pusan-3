import type { ReactNode } from "react";

import { cn } from "@/lib/cn";

/** 디자인 시스템 §7 `card` — 경계는 그림자가 아니라 line 1px 이 만든다 (문서 §6). */
export function Card({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div className={cn("border-line rounded-card bg-surface border p-4", className)}>
      {children}
    </div>
  );
}

/**
 * 디자인 시스템 §7 `card-failed`.
 *
 * 🚨 실패를 빨강으로 칠하지 않는다 (문서 §3). Agent 2개 중 1개만 성공해도 화면은 나가야 하는데
 *    (NF-06) 그걸 빨강으로 칠하면 부모가 매일 빨간 화면을 본다. danger 는 알레르기에만 남긴다.
 */
export function CardFailed({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div
      role="status"
      className={cn("rounded-card bg-surface-muted text-ink-muted text-body-sm p-4", className)}
    >
      {children}
    </div>
  );
}
