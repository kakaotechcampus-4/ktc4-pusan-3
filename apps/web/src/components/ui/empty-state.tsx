import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

import { cn } from "@/lib/cn";

import { ICON_STROKE } from "./icon";

/**
 * 디자인 시스템 §7 `empty-state`.
 *
 * 🚨 **빈 상태를 사과문으로 쓰지 않는다.** "아직 기억이 없어요" 가 아니라
 *    **쌓인 기록 건수를 그대로** 보여준다 (CLAUDE.md §2 — 근거가 없으면 없다고 말한다).
 *    `count` 를 필수로 받는 이유가 그것이다. 0건도 정보다.
 *
 * 아이콘은 40px 인데 §7 의 크기 3단계(16·20·24) 밖이라 여기서만 쓴다 —
 * 빈 화면 한가운데 놓이는 유일한 자리라 24px 는 장식으로도 안 보인다.
 */
const EMPTY_ICON_PX = 40;

export function EmptyState({
  icon: Icon,
  title,
  description,
  count,
  countLabel = "쌓인 기록",
  action,
  className,
}: {
  icon: LucideIcon;
  title: string;
  description: string;
  /** 🚨 지금까지 쌓인 건수. 숨기지 않는다. */
  count: number;
  countLabel?: string;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "bg-surface-muted rounded-card flex flex-col items-center px-4 py-8 text-center",
        className,
      )}
    >
      {/* 빈 화면에서 유일한 색이다. 🚨 사과하는 화면이 아니라 시작하는 화면이라(문서 §7)
          아이콘까지 회색이면 "아직 아무것도 없다" 가 아니라 "여기는 비활성" 으로 읽힌다. */}
      <Icon aria-hidden size={EMPTY_ICON_PX} strokeWidth={ICON_STROKE} className="text-brand" />
      <p className="text-section text-ink mt-3">{title}</p>
      <p className="text-body-sm text-ink-muted mt-1">{description}</p>
      <p className="text-caption text-ink-subtle mt-3">
        {countLabel} {count}건
      </p>
      {action ? <div className="mt-4 w-full">{action}</div> : null}
    </div>
  );
}
