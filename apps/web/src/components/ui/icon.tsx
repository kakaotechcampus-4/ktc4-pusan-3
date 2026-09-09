import { Blocks, BookOpen, HeartPulse, Utensils, type LucideIcon } from "lucide-react";

import type { Agent } from "@/lib/api/types";

/**
 * 아이콘 크기·굵기·도메인 매핑을 한 곳에 고정한다.
 * lucide-react 는 Next 16 기본 optimizePackageImports 목록에 있어서
 * 배럴 임포트를 그대로 써도 트리셰이킹된다 (next.config.ts 설정 불필요).
 */

export const ICON_SIZE = { sm: 16, md: 20, lg: 24 } as const;
export type IconSize = keyof typeof ICON_SIZE;

/** lucide 기본값 2 는 16px 에서 두껍게 보인다. 한 값으로 고정해 화면마다 달라지지 않게 한다. */
export const ICON_STROKE = 1.75;

/** 🚨 도메인 4종과 1:1. suggestion_agent enum 이 바뀌면 여기서 타입 에러가 난다. */
export const DOMAIN_ICON: Record<Agent, LucideIcon> = {
  food: Utensils,
  activity: Blocks,
  education: BookOpen,
  health: HeartPulse,
};

/**
 * 🚨 도메인 아이콘을 단독 신호로 쓰지 않는다 (디자인 시스템 §3).
 * 그래서 이 컴포넌트는 `aria-hidden` 이다 — 의미는 항상 옆의 텍스트 라벨이 진다.
 * 적록색약에서 food(테라코타) 와 health(플럼) 가 가까워지는데, 아이콘까지 장식이면 구분이 사라진다.
 */
export function DomainIcon({
  agent,
  size = "sm",
  className,
}: {
  agent: Agent;
  size?: IconSize;
  className?: string;
}) {
  const Icon = DOMAIN_ICON[agent];
  return (
    <Icon aria-hidden size={ICON_SIZE[size]} strokeWidth={ICON_STROKE} className={className} />
  );
}
