import { DomainIcon } from "@/components/ui/icon";
import type { Agent, ObservationKind } from "@/lib/api/types";
import { cn } from "@/lib/cn";

/**
 * 도메인 4종의 화면 표현. `components/ui/` 가 아니라 여기 있는 이유: 도메인 타입(`Agent`)을 안다.
 * primitive 는 토큰만 알아야 한다 (apps/web/CLAUDE.md §2).
 *
 * 🚨 **도메인 색은 "이 제안이 어느 Agent 에서 왔는가" 하나만 뜻한다** (문서 §3). 칩으로도 쓰고
 *    열린 제안 줄의 **바탕**으로도 쓰지만, 뜻은 같다 — 예쁘라고 칠하는 자리는 없다.
 * 🚨 **단독 신호가 될 수 없다.** 항상 아이콘 + 텍스트 라벨과 함께 나간다. 적록색약에서
 *    food(테라코타)와 health(플럼)가 가까워지는 자리를 라벨이 받는다.
 * 🚨 **한 화면에 도메인 색은 2개까지.** Agent 가 최대 2개(NF-01)라 자연히 지켜지고,
 *    제안 줄은 한 번에 하나만 열리므로 색 면은 항상 하나다.
 */
const DOMAIN_LABEL: Record<Agent, string> = {
  food: "식사",
  activity: "놀이",
  growth: "성장",
  health: "건강",
};

/** `cn()` 은 tailwind-merge 가 아니라 뒤 클래스가 앞을 덮지 않는다. 조합을 통째로 고른다. */
const DOMAIN_FIELD: Record<Agent, string> = {
  food: "bg-food-soft text-food-ink",
  activity: "bg-activity-soft text-activity-ink",
  growth: "bg-growth-soft text-growth-ink",
  health: "bg-health-soft text-health-ink",
};

const DOMAIN_INK: Record<Agent, string> = {
  food: "text-food-ink",
  activity: "text-activity-ink",
  growth: "text-growth-ink",
  health: "text-health-ink",
};

const DOMAIN_PRESS: Record<Agent, string> = {
  food: "hover:bg-food-soft active:bg-food-soft",
  activity: "hover:bg-activity-soft active:bg-activity-soft",
  growth: "hover:bg-growth-soft active:bg-growth-soft",
  health: "hover:bg-health-soft active:bg-health-soft",
};

export function domainLabel(agent: Agent): string {
  return DOMAIN_LABEL[agent];
}

/** 색 면 하나 — `{domain}-soft` 바탕 + `{domain}-ink` 글자. 열린 제안 줄의 머리가 이걸 입는다. */
export function domainField(agent: Agent): string {
  return DOMAIN_FIELD[agent];
}

/** 그 면 위의 글자. 🚨 회색 잉크를 얹지 않는다 — 대비를 `canvas` 기준으로 잘못 계산하게 된다. */
export function domainInk(agent: Agent): string {
  return DOMAIN_INK[agent];
}

/** 누르면 그 줄이 무슨 색으로 열릴지 미리 보여준다. */
export function domainPress(agent: Agent): string {
  return DOMAIN_PRESS[agent];
}

/**
 * 디자인 시스템 §7 `chip-domain`.
 *
 * `onField` 는 **자기 도메인 색 위에 앉을 때**다. 같은 `-soft` 바탕이면 칩이 통째로 사라지므로
 * 바탕만 `surface` 로 바꿔 띄운다 (글자색은 그대로 자기 도메인 잉크다).
 */
export function DomainChip({
  agent,
  onField = false,
  className,
}: {
  agent: Agent;
  onField?: boolean;
  className?: string;
}) {
  return (
    <span
      className={cn(
        "text-label min-h-chip inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5",
        onField ? cn("bg-surface", DOMAIN_INK[agent]) : DOMAIN_FIELD[agent],
        className,
      )}
    >
      <DomainIcon agent={agent} />
      {DOMAIN_LABEL[agent]}
    </span>
  );
}

/**
 * 관찰 테이블 이름 → 그 관찰을 읽는 Agent.
 *
 * 🚨 **접미사를 잘라 쓰지 않는다.** 셋은 접미사가 곧 Agent 이름이지만 `observation_education`
 *    은 아니다 — 이 테이블을 읽는 Agent 는 `growth` 다 (`observation_routine` 도 같은 Agent 가
 *    읽는다. 관찰 테이블이 둘인데 Agent 는 하나라 이름이 1:1 로 붙지 않는다).
 */
const OBSERVATION_AGENT: Record<ObservationKind, Agent> = {
  observation_food: "food",
  observation_health: "health",
  observation_education: "growth",
  observation_activity: "activity",
};

export function observationAgent(kind: ObservationKind): Agent {
  return OBSERVATION_AGENT[kind];
}

/**
 * 07 기억 화면의 도메인 표시 — **색이 없다.**
 *
 * 🚨 도메인 색의 뜻은 "이 제안이 **어느 Agent 에서 왔는가**" 하나다 (문서 §3). 07 에는 제안이
 *    없다 — 여기 서 있는 것은 부모가 적은 관찰과 거기서 자란 프로필이라, `chip-domain` 을
 *    쓰면 색이 뜻하지 않는 것을 뜻하게 된다. 게다가 관찰 목록은 4개 도메인이 섞여 내려오므로
 *    (`?domain=` 생략 시 4개 테이블 병합) 칩을 쓰는 순간 **한 화면에 도메인 색 2개**라는
 *    상한(문서 §3)을 목록 하나가 혼자 깨뜨린다.
 *
 * 그래서 아이콘 + 라벨만 뉴트럴 메타로 낸다. 아이콘은 여전히 `aria-hidden` 이고 뜻은 라벨이 진다.
 */
export function DomainMeta({ agent, className }: { agent: Agent; className?: string }) {
  return (
    <span className={cn("inline-flex items-center gap-1", className)}>
      <DomainIcon agent={agent} />
      {DOMAIN_LABEL[agent]}
    </span>
  );
}
