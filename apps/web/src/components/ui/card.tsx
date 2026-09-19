import type { ReactNode } from "react";

import { cn } from "@/lib/cn";

/**
 * 디자인 시스템 §7 `card` — 경계는 그림자가 아니라 line 1px 이 만든다 (문서 §6).
 *
 * `accent` 는 테두리만 `brand` 로 바꾼 같은 카드다. 화면에서 **한 장**만, 지금 눈여겨볼 것에
 * 쓴다 — 두 장이 되는 순간 강조가 아니라 장식이 된다.
 *
 * 🚨 `accent` 를 `brand-soft` 로 칠하지 않는다. 그 배경은 **개인화 추천 카드**의 뜻이고
 *    (문서 §3), 근거 칩 없이 같은 배경을 쓰면 추천이 아닌 것이 추천처럼 보인다.
 * 🚨 테두리 색을 `className` 으로 덮어쓰지 않는다 — `cn()` 은 tailwind-merge 가 아니라
 *    어느 쪽이 이길지가 Tailwind 의 정렬 순서에 달린다. 변형을 여기에 추가할 것.
 */
export type CardTone = "default" | "accent";

const TONE: Record<CardTone, string> = {
  default: "border-line",
  accent: "border-brand",
};

export function Card({
  children,
  className,
  tone = "default",
}: {
  children: ReactNode;
  className?: string;
  tone?: CardTone;
}) {
  return (
    <div className={cn("rounded-card bg-surface border p-4", TONE[tone], className)}>
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
