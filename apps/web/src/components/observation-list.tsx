"use client";

import { ChevronRight } from "lucide-react";

import { DomainMeta, observationAgent } from "@/components/domain-chip";
import { ICON_SIZE, ICON_STROKE } from "@/components/ui/icon";
import { isHealthObservation, type Observation } from "@/lib/api/types";

/**
 * 07 관찰 목록 — **줄**이다. 카드가 아니다.
 *
 * 🚨 이 화면의 핵심 구분이 여기 있다. 관찰은 가는 선으로만 나뉜 **한 덩어리 줄 목록**이고,
 *    프로필은 따로 서는 **카드**다 (`AffinityList`). 프로토타입처럼 둘을 같은 모양으로 그리고
 *    라벨로만 가르면 "한 번 본 것" 과 "확정된 성향" 이 같은 무게로 읽히는데, 그게 이 제품의
 *    1번 규칙(한 번의 관찰을 성향으로 확정하지 않는다 · CLAUDE.md §2)이 화면에서 사라지는
 *    지점이다. 다섯 줄이 카드 한 장을 만들었다는 사실이 **배치만으로** 보여야 한다.
 *
 * 🚨 **줄을 끌고 가는 것은 부모가 적은 말(`raw_text`)이다.** 도메인도 날짜도 그 아래 메타다.
 *
 * 🚨 **날짜를 만들지 않는다.** `observed_label`("오늘" · "3일 전")은 서버 문구다 (CLAUDE.md §3).
 *    없으면 그 자리를 비운다 — 프론트가 `observed_to` 로 계산해 채우지 않는다.
 *
 * 🚨 **도메인 색을 쓰지 않는다** (`DomainMeta` 주석). 07 에는 Agent 결과가 없고, 목록은
 *    4개 도메인이 섞여 내려온다.
 */
export function ObservationList({
  observations,
  onOpen,
}: {
  observations: Observation[];
  onOpen: (observation: Observation) => void;
}) {
  return (
    <ul className="border-line divide-line rounded-card bg-surface divide-y overflow-hidden border">
      {observations.map((observation) => (
        <li key={`${observation.kind}:${observation.id}`}>
          <ObservationRow observation={observation} onOpen={() => onOpen(observation)} />
        </li>
      ))}
    </ul>
  );
}

function ObservationRow({ observation, onOpen }: { observation: Observation; onOpen: () => void }) {
  return (
    <button
      type="button"
      onClick={onOpen}
      className="min-h-touch ease-standard active:bg-surface-muted flex w-full items-start gap-2 px-4 py-3 text-left transition-colors duration-120 focus-visible:-outline-offset-2"
    >
      <span className="flex min-w-0 flex-1 flex-col gap-1">
        <span className="text-body text-ink">{observation.raw_text}</span>

        {/* 🚨 띄운 가운뎃점은 **줄당 하나**다 (문서 §4). 도메인과 날짜가 한 줄, 아래 줄은 따로. */}
        <span className="text-caption text-ink-subtle flex flex-wrap items-center gap-1">
          <DomainMeta agent={observationAgent(observation.kind)} />
          {observation.observed_label ? <span>· {observation.observed_label}</span> : null}
        </span>

        <ObservationLink observation={observation} />
      </span>

      <ChevronRight
        aria-hidden
        size={ICON_SIZE.md}
        strokeWidth={ICON_STROKE}
        className="text-ink-subtle mt-0.5 shrink-0"
      />
    </button>
  );
}

/**
 * 관찰이 무엇과 이어져 있는지 한 줄.
 *
 * 🚨 **health 는 모양이 다르다** — `subject` · `polarity` · `affinity` 키가 **아예 없다.**
 *    `null` 검사가 아니라 `kind === "observation_health"` 로 분기한다 (apps/web/CLAUDE.md §4).
 *    승격 파이프라인 밖이라 묶일 프로필이 없고, 대신 증상이 그 자리를 받는다.
 */
function ObservationLink({ observation }: { observation: Observation }) {
  if (isHealthObservation(observation)) {
    const symptoms = observation.domain_fields.symptom;
    if (!symptoms || symptoms.length === 0) return null;
    return <span className="text-caption text-ink-muted">증상 · {symptoms.join(", ")}</span>;
  }

  if (!observation.affinity) return null;
  return (
    <span className="text-caption text-ink-muted">프로필 · {observation.affinity.merge_key}</span>
  );
}
