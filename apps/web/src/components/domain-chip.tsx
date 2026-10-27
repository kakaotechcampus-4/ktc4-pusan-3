import { AlarmClock, Blocks, BookA, Thermometer, Utensils, type LucideIcon } from "lucide-react";

import { DomainIcon, ICON_SIZE, ICON_STROKE, type IconSize } from "@/components/ui/icon";
import type { Agent, ObservationKind } from "@/lib/api/types";
import { cn } from "@/lib/cn";

/**
 * 도메인 4종의 화면 표현. `components/ui/` 가 아니라 여기 있는 이유: 도메인 타입(`Agent`)을 안다.
 * primitive 는 토큰만 알아야 한다 (apps/web/CLAUDE.md §2).
 *
 * 🚨 **도메인 색은 "이 제안이 어느 Agent 에서 왔는가" 하나만 뜻한다** (문서 §3). 칩으로도 쓰고
 *    아이콘 타일의 바탕으로도 쓰지만, 뜻은 같다 — 예쁘라고 칠하는 자리는 없다.
 * 🚨 **단독 신호가 될 수 없다.** 항상 아이콘 + 텍스트 라벨과 함께 나간다. 적록색약에서
 *    food(테라코타)와 health(플럼)가 가까워지는 자리를 라벨이 받는다.
 * 🚨 **한 화면에 도메인 색은 2개까지.** Agent 가 최대 2개(NF-01)라 자연히 지켜진다.
 * 🚨 **큰 면을 이 색으로 칠하려면 그 화면에 그 면이 몇 개까지 서는지 먼저 센다.**
 *    05 의 열린 제안 줄이 이 바탕을 입고 있었는데, Agent 당 후보가 3가지가 되면서 면이 여섯이
 *    됐다 — "한 화면에 2개" 는 칩을 세던 규칙이라 면으로 채우면 화면이 색 덩어리가 된다 (#151).
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

/**
 * 카드 왼쪽에 서는 **색 띠**. 🚨 `-soft` 가 아니라 **진한 쪽**이다 — 4px 밖에 안 되는 면이라
 * 연한 색을 쓰면 `line`(1px 테두리)과 구분이 안 되고, 그러면 색이 있는지도 모르게 된다.
 */
const DOMAIN_BAR: Record<Agent, string> = {
  food: "bg-food",
  activity: "bg-activity",
  growth: "bg-growth",
  health: "bg-health",
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

/** 색 면 하나 — `{domain}-soft` 바탕 + `{domain}-ink` 글자. 07 의 아이콘 타일이 이걸 입는다. */
export function domainField(agent: Agent): string {
  return DOMAIN_FIELD[agent];
}

/** 카드 왼쪽 색 띠 (위 머리말). 🚨 띠는 **모양이 없는 면**이라 이름은 늘 글자가 따로 진다. */
export function domainBar(agent: Agent): string {
  return DOMAIN_BAR[agent];
}

/** 눌러서 가는 곳이 그 도메인의 제안이라, 누른 색을 그 도메인 색으로 준다. */
export function domainPress(agent: Agent): string {
  return DOMAIN_PRESS[agent];
}

/**
 * 디자인 시스템 §7 `chip-domain`.
 *
 * ⚠️ 자기 도메인 색 위에 앉을 때 바탕만 `surface` 로 바꿔 띄우는 `onField` 가 있었다.
 * 그 자리(05 의 열린 제안 줄 머리)가 없어져서 뗐다 (#151) — 같은 `-soft` 위에 같은 `-soft` 칩을
 * 놓는 자리가 다시 생기면 그때 되살린다.
 */
export function DomainChip({ agent, className }: { agent: Agent; className?: string }) {
  return (
    <span
      className={cn(
        "text-label min-h-chip inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5",
        DOMAIN_FIELD[agent],
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
  observation_routine: "growth",
};

export function observationAgent(kind: ObservationKind): Agent {
  return OBSERVATION_AGENT[kind];
}

/**
 * 기록 한 건의 이름. 셋은 Agent 이름(`DOMAIN_LABEL`)과 같고 **growth 만 둘로 갈린다** (#501).
 *
 * 🚨 **기록 한 건을 그리는 자리에서만 쓴다** (07 · 09 기록 줄 · 기록 상세 · 04 기록 줄). 07 분류 필터 ·
 *    05 제안 · 04 Agent 블록은 Agent 를 고르거나 Agent 가 낸 것이라 그대로 "성장" 이다 — 서버도
 *    `?domain=growth` · `agent: "growth"` 하나로 받고 보낸다.
 * 🚨 **색은 여전히 `observationAgent()` 로 고른다.** 교육 · 생활습관 둘 다 성장 색이다 (아래 `OBSERVATION_ICON` 주석).
 */
const OBSERVATION_LABEL: Record<ObservationKind, string> = {
  observation_food: DOMAIN_LABEL.food,
  observation_health: DOMAIN_LABEL.health,
  observation_education: "교육",
  observation_activity: DOMAIN_LABEL.activity,
  observation_routine: "생활습관",
};

export function observationLabel(kind: ObservationKind): string {
  return OBSERVATION_LABEL[kind];
}

/**
 * 기록 한 건의 아이콘. 셋은 그 기록을 읽는 Agent 의 아이콘(`DOMAIN_ICON`)과 같다.
 *
 * 🚨 **growth 만 둘로 갈린다** (#501). 둘 다 책으로 그리면 "양치" 기록이 책 옆에 섰다.
 *    - 교육은 **글자가 적힌 책**(`BookA`) — 성장 Agent 의 책(`BookOpen`)과 한 집안이라, "성장" 필터를
 *      고르고 내려온 목록에서 어디 속한 기록인지 모양으로 이어진다
 *    - 생활습관은 **자명종**(`AlarmClock`) — 양치 · 수면처럼 하루에 정해진 때가 있는 일이다
 *    둘 다 도메인 네 아이콘처럼 **집에 있는 물건**이다 (디자인 시스템 §7 아이콘).
 * 🚨 **색은 갈리지 않는다.** 두 기록 다 성장 색이다 — 도메인 색은 "어느 Agent" 를 뜻하고(§3),
 *    Agent 는 여전히 하나다. 그래서 둘을 가르는 것은 이 모양과 옆의 라벨뿐이다.
 * ⚠️ `components/ui/icon.tsx` 가 아니라 여기 있는 이유: 관찰 테이블 이름은 도메인 타입이라 primitive 가
 *    몰라야 하고(apps/web/CLAUDE.md §2), 라벨 표와 한 파일에 있어야 둘이 어긋나지 않는다.
 */
export const OBSERVATION_ICON: Record<ObservationKind, LucideIcon> = {
  observation_food: Utensils,
  observation_health: Thermometer,
  observation_education: BookA,
  observation_activity: Blocks,
  observation_routine: AlarmClock,
};

/** 기록 한 건의 아이콘. `DomainIcon` 과 같이 `aria-hidden` 이다 — 뜻은 옆의 라벨이 진다. */
export function ObservationIcon({
  kind,
  size = "sm",
  className,
}: {
  kind: ObservationKind;
  size?: IconSize;
  className?: string;
}) {
  const Icon = OBSERVATION_ICON[kind];
  return (
    <Icon aria-hidden size={ICON_SIZE[size]} strokeWidth={ICON_STROKE} className={className} />
  );
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

/** `DomainMeta` 의 기록 한 건 판. 성장 기록은 교육 · 생활습관으로 갈려 선다 (`observationLabel`). */
export function ObservationMeta({
  kind,
  className,
}: {
  kind: ObservationKind;
  className?: string;
}) {
  return (
    <span className={cn("inline-flex items-center gap-1", className)}>
      <ObservationIcon kind={kind} />
      {OBSERVATION_LABEL[kind]}
    </span>
  );
}
