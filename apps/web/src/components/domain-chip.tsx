import { DomainIcon } from "@/components/ui/icon";
import type { Agent } from "@/lib/api/types";
import { cn } from "@/lib/cn";

/**
 * 디자인 시스템 §7 `chip-domain` — 어느 Agent 의 결과인가.
 *
 * `components/ui/` 가 아니라 여기 있는 이유: 도메인 타입(`Agent`)을 안다.
 * primitive 는 토큰만 알아야 한다 (apps/web/CLAUDE.md §2).
 *
 * 🚨 **아이콘은 `aria-hidden` 이고 의미는 라벨이 진다** (문서 §3). 색과 아이콘은 보조 신호다 —
 *    적록색약에서 food(테라코타)와 health(플럼)가 가까워지는 자리를 라벨이 받는다.
 * 🚨 **한 화면에 도메인 색을 2개까지만 쓴다** (문서 §3). Agent 가 최대 2개라 자연히 지켜지지만,
 *    목록을 만들 때 4개를 다 늘어놓지 말 것.
 */
const DOMAIN_LABEL: Record<Agent, string> = {
  food: "식사",
  activity: "놀이",
  education: "교육",
  health: "건강",
};

/** `cn()` 은 tailwind-merge 가 아니라 뒤 클래스가 앞을 덮지 않는다. 조합을 통째로 고른다. */
const DOMAIN_TONE: Record<Agent, string> = {
  food: "bg-food-soft text-food-ink",
  activity: "bg-activity-soft text-activity-ink",
  education: "bg-education-soft text-education-ink",
  health: "bg-health-soft text-health-ink",
};

export function domainLabel(agent: Agent): string {
  return DOMAIN_LABEL[agent];
}

export function DomainChip({ agent, className }: { agent: Agent; className?: string }) {
  return (
    <span
      className={cn(
        "text-label min-h-chip inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5",
        DOMAIN_TONE[agent],
        className,
      )}
    >
      <DomainIcon agent={agent} />
      {DOMAIN_LABEL[agent]}
    </span>
  );
}
