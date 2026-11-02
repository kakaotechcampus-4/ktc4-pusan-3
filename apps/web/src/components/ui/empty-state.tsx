import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

import { cn } from "@/lib/cn";

import { SkeletonGroup, SkeletonText } from "./skeleton";

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
  "data-tour": tour,
}: {
  icon: LucideIcon;
  title: string;
  description: string;
  /** 🚨 지금까지 쌓인 건수. 숨기지 않는다. */
  count: number;
  countLabel?: string;
  action?: ReactNode;
  className?: string;
  /** 화면 투어가 비추는 자리 (`components/screen-tour.tsx`). */
  "data-tour"?: string;
}) {
  return (
    <div
      data-tour={tour}
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

/**
 * 받는 동안, **빈 상태가 올 것이 거의 확실한 자리**에 쓴다 (02 아이 정보의 알레르기 — 막 만든 아이다).
 * `EmptyState` 와 같은 상자 · 같은 줄 높이라 빈 상태가 오면 그 자리에 글자가 들어온다.
 * 🚨 건수를 지어내지 않는다 — 건수 줄도 자리만 잡는다. 아이콘 자리는 비워 둔다(장식이 아니라 뜻이 없는 빈칸이다).
 */
export function EmptyStateSkeleton({
  label = "불러오는 중",
  descriptionLines = 1,
}: {
  label?: string;
  /** 진짜 설명이 그 폭에서 몇 줄로 흐르는지. */
  descriptionLines?: 1 | 2;
}) {
  return (
    <SkeletonGroup
      label={label}
      className="bg-surface-muted rounded-card flex flex-col items-center px-4 py-8 text-center"
    >
      <span aria-hidden className="block" style={{ height: EMPTY_ICON_PX }} />
      <SkeletonText size="section" width="1/2" className="mt-3 w-full" />
      <SkeletonText size="body-sm" width="3/4" className="mt-1 w-full" />
      {descriptionLines === 2 ? (
        <SkeletonText size="body-sm" width="1/2" className="w-full" />
      ) : null}
      <SkeletonText size="caption" width="1/4" className="mt-3 w-full" />
    </SkeletonGroup>
  );
}
