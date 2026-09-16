"use client";

import { ChevronRight } from "lucide-react";

import { domainField, domainLabel, observationAgent } from "@/components/domain-chip";
import { DOMAIN_ICON, ICON_SIZE, ICON_STROKE } from "@/components/ui/icon";
import { IconTile } from "@/components/ui/icon-tile";
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
 * 줄의 뼈대는 세 가지 **다른 종류**다 — 왼쪽 타일(어느 영역인지) · 문장 · 그 아래 한 줄.
 * 전부 같은 크기의 글자로 쌓으면 훑을 기준선이 없어서 목록이 통째로 회색 덩어리로 읽힌다.
 * 🚨 **타일은 그 영역의 도메인 색이다** (문서 §3 · §7 "07 기록 줄 · 기억 카드").
 *    도메인 색의 뜻을 "어느 Agent 결과인가" 에서 **"어느 영역인가"** 로 넓히면서 열린 자리다 —
 *    07 은 제안이 아니라 **쌓인 것을 훑는 화면**이고, 목록이 네 영역을 섞어 내려주므로
 *    "한 화면에 도메인 색 2개" 상한의 예외이기도 하다 (그 상한은 제안 화면의 규칙이다).
 *    🚨 **브랜드는 여전히 못 쓴다.** "어디서 왔나"(도메인) 와 "무엇을 하는가"(브랜드) 를
 *    같은 색으로 쓰지 않는다 — 여기 서 있는 것은 고르는 버튼이 아니다.
 *    🚨 **색이 단독 신호가 되지 않는다** — 타일 옆에 영역 이름이 항상 글자로 선다.
 *
 * 🚨 **이 줄이 무엇으로 이어졌는지는 칩이 진다.** "이 기록이 어느 기억에 묶였나" 는 이 제품의
 *    논지 자체(기록이 쌓여 기억이 된다)라, 날짜와 같은 무게의 회색 글자로 두면 목록에서
 *    제일 약한 것이 제일 중요한 사실이 된다.
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
  const agent = observationAgent(observation.kind);

  return (
    <button
      type="button"
      onClick={onOpen}
      className="min-h-touch ease-standard active:bg-surface-muted flex w-full items-start gap-3 px-4 py-3.5 text-left transition-colors duration-120 focus-visible:-outline-offset-2"
    >
      <IconTile icon={DOMAIN_ICON[agent]} tone="plain" className={domainField(agent)} />

      <span className="flex min-w-0 flex-1 flex-col gap-1.5">
        <span className="text-body text-ink">{observation.raw_text}</span>

        {/* 🚨 띄운 가운뎃점은 **줄당 하나**다 (문서 §4). 도메인과 날짜가 한 줄, 칩은 아래 줄. */}
        <span className="text-caption text-ink-subtle">
          {domainLabel(agent)}
          {observation.observed_label ? ` · ${observation.observed_label}` : ""}
        </span>

        <ObservationLink observation={observation} />
      </span>

      <ChevronRight
        aria-hidden
        size={ICON_SIZE.md}
        strokeWidth={ICON_STROKE}
        className="text-ink-subtle mt-2 shrink-0"
      />
    </button>
  );
}

/** 줄 아래에 하나만 서는 작은 표식. 글자 줄이 아니라 **모양**이라 메타와 안 섞인다. */
function LinkChip({ label, value }: { label: string; value: string }) {
  return (
    // 🚨 `truncate` 로 잘라 맞추지 않는다 (문서 §10). 글자를 키우면 칩이 두 줄이 될지언정
    //    묶인 기억 이름이 잘리지는 않는다 — 이름이 반만 보이면 무엇에 묶였는지 알 수 없다.
    <span className="bg-surface-muted text-ink-muted text-caption min-h-chip inline-flex w-fit max-w-full items-center gap-1 rounded-full px-2.5 py-1">
      {/* 띄운 가운뎃점은 줄당 하나다 (문서 §4). 이 칩은 자기 줄이라 위 메타 줄과 겹치지 않는다. */}
      <span className="text-ink-subtle shrink-0">{label} ·</span>
      <span>{value}</span>
    </span>
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
    return <LinkChip label="증상" value={symptoms.join(", ")} />;
  }

  if (!observation.affinity) return null;
  return <LinkChip label="기억" value={observation.affinity.merge_key} />;
}
