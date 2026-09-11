"use client";

import { PenLine, Sprout } from "lucide-react";

import { domainLabel } from "@/components/domain-chip";
import { Button } from "@/components/ui/button";
import { Card, CardFailed } from "@/components/ui/card";
import { DomainIcon } from "@/components/ui/icon";
import { IconTile } from "@/components/ui/icon-tile";
import { ProgressSteps } from "@/components/ui/progress-steps";
import type { RunState } from "@/hooks/use-run-stream";
import {
  isHealthObservation,
  type Agent,
  type ConfidenceSource,
  type Observation,
} from "@/lib/api/types";

/**
 * 04 저장 결과 — run 이벤트를 화면으로 옮긴다.
 *
 * 🚨 **별도 라우트가 아니다.** 03 홈과 같은 컴포넌트 트리 안의 레이어다 —
 *    화면을 벗어나면 `useRunStream` 이 스트림을 끊고, 실패 시 입력창에 되돌릴 **원문의 정본이
 *    입력 화면이 들고 있는 값**이기 때문이다 (apps/web/CLAUDE.md §3).
 *
 * 🚨 **`partial` 은 실패 화면이 아니다** (NF-06). Agent 2개 중 1개만 성공해도 그 화면을 보여주고,
 *    성공과 실패를 **한 화면에** 섞는다. `done` 이 와도 `partial` 이 있었으면 부분 결과다.
 * 🚨 **실패를 빨강으로 칠하지 않는다** (문서 §3). `CardFailed` 는 `surface-muted` 다.
 * 🚨 **health 관찰은 모양이 다르다.** `subject` · `polarity` · `affinity` 키 자체가 없다 —
 *    `null` 검사가 아니라 `isHealthObservation()` 으로 분기한다.
 */

/** 진행 중. 단계 문구는 서버가 보낸 `step.label` 을 그대로 쓴다. */
export function RunProgress({ state }: { state: RunState }) {
  return (
    <div className="flex flex-1 flex-col justify-center gap-6">
      <div>
        <IconTile icon={PenLine} className="mb-3" />
        <h2 className="text-title text-ink">적어주신 말을 보고 있어요</h2>
        <p className="text-body-sm text-ink-muted mt-2">
          저장이 끝나야 다음으로 넘어가요. 잘못 저장하지 않으려고 한 칸씩 확인해요.
        </p>
      </div>

      <Card>
        <ProgressSteps
          index={state.step?.index ?? 0}
          total={state.step?.total ?? 3}
          label={state.step?.label ?? "시작하고 있어요"}
        />
      </Card>
    </div>
  );
}

export function RunResult({
  state,
  inputText,
  onRetry,
  onEdit,
  onDone,
  onPickOffer,
}: {
  state: RunState;
  /** 부모가 적은 원문. 🚨 훅이 아니라 입력 화면이 들고 있는 값이다. */
  inputText: string;
  onRetry: () => void;
  onEdit: () => void;
  onDone: () => void;
  onPickOffer: (agents: Agent[]) => void;
}) {
  if (state.failure) {
    return (
      <div className="flex flex-col gap-4">
        <div>
          <p className="text-label text-brand">저장 결과</p>
          <h2 className="text-title text-ink mt-1">읽지 못했어요</h2>
          <p className="text-body-sm text-ink-muted mt-2">
            한 줄을 구조화하는 데 실패했어요. 잘못 저장하지 않으려고 아무것도 저장하지 않았어요.
          </p>
        </div>

        <CardFailed>
          <p>적어주신 말은 입력창에 그대로 남겨뒀어요.</p>
        </CardFailed>

        <div className="flex flex-wrap gap-2">
          <Button onClick={onRetry}>다시 시도</Button>
          <Button variant="secondary" onClick={onEdit}>
            직접 고쳐 쓰기
          </Button>
        </div>
      </div>
    );
  }

  const noChildObservation = state.observations.length === 0;

  return (
    <div className="flex flex-col gap-4">
      <div>
        <p className="text-label text-brand">저장 결과</p>
        <h2 className="text-title text-ink mt-1">이렇게 저장했어요</h2>
        {inputText ? (
          <p className="text-body-sm text-ink-muted mt-2">적어주신 한 줄: {inputText}</p>
        ) : null}
      </div>

      {state.partial ? (
        <CardFailed>
          <p>
            {state.partial.failed.map(domainLabel).join(", ")} 쪽은 이번에 준비하지 못했어요. 아래
            결과는 그대로 저장됐어요.
          </p>
        </CardFailed>
      ) : null}

      {noChildObservation ? (
        <CardFailed>
          <p>
            아이에 관한 관찰은 찾지 못했어요. 적어주신 말은 그대로 두고, 기억으로는 저장하지
            않았어요.
          </p>
        </CardFailed>
      ) : (
        <section className="flex flex-col gap-3">
          <h3 className="text-label text-brand">관찰 {state.observations.length}건 저장됨</h3>
          {state.observations.map((observation) => (
            <ObservationCard key={observation.id} observation={observation} />
          ))}
        </section>
      )}

      {state.promoted.length > 0 ? (
        <section className="flex flex-col gap-3">
          <h3 className="text-label text-brand">기억이 자랐어요</h3>
          {state.promoted.map((change) => (
            <Card key={change.ref.id}>
              <div className="flex items-start gap-3">
                <IconTile icon={Sprout} />
                <div className="min-w-0">
                  <p className="text-body text-ink">{change.merge_key}</p>
                  <p className="text-body-sm text-ink-muted mt-1">{change.state_reason}</p>
                </div>
              </div>
            </Card>
          ))}
        </section>
      ) : null}

      <p className="text-caption text-ink-subtle">
        한 번의 행동은 성향으로 확정하지 않아요. 반복 횟수는 코드가 셉니다.
      </p>

      {state.offers.length > 0 ? (
        <section className="flex flex-col gap-3">
          <h3 className="text-label text-brand">이것도 도와드릴까요?</h3>
          {/* 🚨 Agent 는 최대 2개다 (NF-01). 서버가 고른 것만 그대로 보여준다.
              🚨 여기는 primary 를 쓰지 않는다 — 나란히 놓인 선택지라 둘 중 하나를 다음 행동으로
                 세우면 안 된다 (문서 §7 "한 화면에 primary 는 하나"). 기본값은 아래의 기록만이다. */}
          {state.offers.slice(0, 2).map((offer) => (
            <Button
              key={offer.agent}
              variant="secondary"
              block
              onClick={() => onPickOffer([offer.agent])}
            >
              {offer.label}
            </Button>
          ))}
          <Button variant="tertiary" onClick={onDone}>
            아니요, 기록만
          </Button>
          <p className="text-caption text-ink-subtle">
            아무것도 누르지 않고 닫아도 기록은 남아요. 기본값은 기록만이에요.
          </p>
        </section>
      ) : (
        <Button variant="secondary" block onClick={onDone}>
          홈으로
        </Button>
      )}
    </div>
  );
}

/** 발화의 출처. 서버 enum 을 화면 문구로 옮기는 표다 — 추측을 섞지 않는다. */
const CONFIDENCE_LABEL: Record<ConfidenceSource, string> = {
  institution_notice: "기관 공지",
  parent_direct: "보호자 직접",
  parent_hedged: "보호자 추측",
  parent_hearsay: "전해 들음",
};

/** 관찰 4테이블 ↔ 도메인. health 는 승격 파이프라인 밖이라 모양이 다르다. */
const OBSERVATION_AGENT: Record<Observation["kind"], Agent> = {
  observation_food: "food",
  observation_activity: "activity",
  observation_education: "education",
  observation_health: "health",
};

function ObservationCard({ observation }: { observation: Observation }) {
  const agent = OBSERVATION_AGENT[observation.kind];

  return (
    <Card>
      {/* 🚨 여기서는 **도메인 색을 쓰지 않는다** (`DomainChip` 이 아니다). 한 화면에 도메인 색은
          2개까지인데(문서 §3), 한 줄이 관찰 3건으로 갈리면 4색이 다 뜰 수 있다. 그리고 도메인 색은
          "어느 Agent 의 결과인가" 신호라(05 제안) 저장된 관찰에 쓰면 그 뜻이 흐려진다.
          모양은 아이콘이, 뜻은 라벨이 진다. */}
      <div className="text-label text-ink-muted flex flex-wrap items-center gap-1.5">
        <DomainIcon agent={agent} className="text-ink-subtle" />
        {domainLabel(agent)}
        {observation.observed_label ? (
          <span className="text-caption text-ink-subtle">· {observation.observed_label}</span>
        ) : null}
      </div>

      <p className="text-body text-ink mt-3">{observation.raw_text}</p>

      {isHealthObservation(observation) ? (
        <p className="text-body-sm text-ink-muted mt-1">
          {[observation.domain_fields.symptom?.join(", "), observation.domain_fields.observed_time]
            .filter(Boolean)
            .join(", ")}
        </p>
      ) : (
        <p className="text-body-sm text-ink-muted mt-1">{observation.subject}</p>
      )}

      <p className="text-caption text-ink-subtle mt-2">
        {CONFIDENCE_LABEL[observation.confidence_source]}
      </p>
    </Card>
  );
}
