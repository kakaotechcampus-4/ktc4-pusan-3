import type { ReactNode } from "react";

import { cn } from "@/lib/cn";

/**
 * 디자인 시스템 §7 배너.
 *
 * 🚨 **두 색은 쓰는 자리가 정해져 있다** (문서 §3). 늘리지 말 것.
 *    - `caution` = 승인 게이트 2곳 전용. "내가 확인해야 한다" 를 뜻한다 (06 의 `prechecks`).
 *    - `danger` = 알레르기 경고 · 건강 추천 중단 · 파괴적 확정 전용.
 *
 * 🚨 **실패는 배너가 아니다.** `failed` · `partial` 의 실패 쪽은 `CardFailed` 다 —
 *    빨강으로 칠하면 부모가 매일 빨간 화면을 본다 (문서 §3).
 *
 * 배너는 화면 최상단 한 곳에만 둔다. 둘이 동시에 필요하면 `danger` 가 이긴다 —
 * 고르는 것은 화면 쪽 책임이라 이 컴포넌트는 받은 것만 그린다.
 */
export type BannerTone = "caution" | "danger";

const TONE: Record<BannerTone, string> = {
  caution: "bg-caution-soft border-l-caution text-caution-ink",
  danger: "bg-danger-soft border-l-danger text-danger-ink",
};

export function Banner({
  tone,
  title,
  children,
  className,
}: {
  tone: BannerTone;
  /** 없어도 된다. 있으면 본문 위에 한 줄로 얹는다. */
  title?: string;
  children?: ReactNode;
  className?: string;
}) {
  return (
    <div
      // danger 는 읽지 않고 지나가면 안 되는 내용이라 스크린리더에 즉시 알린다.
      role={tone === "danger" ? "alert" : "status"}
      className={cn("rounded-field text-body-sm border-l-[3px] px-3.5 py-3", TONE[tone], className)}
    >
      {title ? <p className="text-section">{title}</p> : null}
      {children ? <div className={cn(title && "mt-1")}>{children}</div> : null}
    </div>
  );
}
