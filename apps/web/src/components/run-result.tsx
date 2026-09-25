"use client";

import { PenLine, Sprout } from "lucide-react";

import { AgentPrompts } from "@/components/agent-prompts";
import { domainLabel, observationAgent } from "@/components/domain-chip";
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
  // 🚨 서버가 끝을 말하지 않고 끝난 run 이다 (20초 침묵 · 끊긴 스트림 · 연결 실패).
  //    **저장 여부를 모른다** — "저장했어요" 도 "저장하지 않았어요" 도 말하지 않는다
  //    (CLAUDE.md §2 · PR #71 리뷰). 원문은 입력창에 그대로 남아 있다 (03 홈 `closeRun`).
  if (state.status === "unconfirmed") {
    return (
      <div className="flex flex-col gap-4">
        <div>
          <h2 className="text-title text-ink">결과를 받지 못했어요</h2>
          <p className="text-body-sm text-ink-muted mt-2">
            연결이 끊겨서 저장됐는지 확인하지 못했어요. 적어주신 말은 입력창에 그대로 남겨뒀어요.
          </p>
        </div>

        <CardFailed>
          {/* 같은 키로 나가는 재시도라 실제로 두 번 저장되지 않는다 (lib/api/idempotency.ts). */}
          <p>다시 시도하면 같은 한 줄로 확인해요. 이미 저장됐다면 두 번 저장되지 않아요.</p>
        </CardFailed>

        {state.observations.length > 0 ? (
          <section>
            <h3 className="text-label text-brand">
              여기까지 받은 기록 {state.observations.length}건
            </h3>
            <Card className="divide-line mt-2 flex flex-col divide-y">
              {state.observations.map((observation) => (
                <ObservationRow key={observation.id} observation={observation} />
              ))}
            </Card>
          </section>
        ) : null}

        <div className="flex flex-wrap gap-2">
          <Button onClick={onRetry}>다시 시도</Button>
          <Button variant="secondary" onClick={onEdit}>
            직접 고쳐 쓰기
          </Button>
        </div>
      </div>
    );
  }

  if (state.failure) {
    return (
      <div className="flex flex-col gap-4">
        <div>
          <h2 className="text-title text-ink">읽지 못했어요</h2>
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
      <h2 className="text-title text-ink">이렇게 저장했어요</h2>

      {/* 이 화면의 주인공은 결과 카드가 아니라 **부모가 적은 말**이다 (관찰 노트).
          화면에서 한 장만 쓰는 `card-accent` 를 여기 쓴다 — 아래는 전부 거기서 나온 것이다. */}
      {inputText ? (
        <Card tone="accent">
          <div className="flex items-start gap-3">
            <IconTile icon={PenLine} />
            <div className="min-w-0">
              <p className="text-caption text-ink-subtle">적어주신 한 줄</p>
              <p className="text-body text-ink mt-1">{inputText}</p>
            </div>
          </div>
        </Card>
      ) : null}

      {/* 🚨 서버가 보낸 partial 만 여기 온다 — 어느 Agent 가 실패했는지 서버가 말해 준 경우다.
          클라이언트가 스스로 끝낸 경우는 위 `unconfirmed` 로 빠진다. */}
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
            아이에 관한 기록은 찾지 못했어요. 적어주신 말은 그대로 두고, 기록으로는 저장하지
            않았어요.
          </p>
        </CardFailed>
      ) : (
        <section>
          <h3 className="text-label text-brand">기록 {state.observations.length}건 저장됨</h3>
          {/* 🚨 관찰마다 카드를 한 장씩 주면 흰 상자가 줄줄이 서서 무엇이 한 덩어리인지 사라진다.
              한 장 안에 가는 선으로 나눈다 — 03 홈의 "오늘" 카드와 같은 방식이다. */}
          <Card className="divide-line mt-2 flex flex-col divide-y">
            {state.observations.map((observation) => (
              <ObservationRow key={observation.id} observation={observation} />
            ))}
          </Card>
        </section>
      )}

      {state.promoted.length > 0 ? (
        <section className="flex flex-col gap-2">
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

      {/* 🚨 제안을 버튼으로 쌓지 않는다. 예전에는 [제안][제안][아니요] 3개가 같은 무게로 서서
          무엇이 다음 행동인지가 없었다 — 고르는 것은 줄(`AgentPromptRow`)이고, 화면을 떠나는
          것만 버튼이다. "아니요" 버튼은 지웠다: 기본값이 기록만이라는 것은 아래 문구가 말하고,
          아무것도 고르지 않아도 이미 저장은 끝나 있다. */}
      {state.offers.length > 0 ? (
        <section className="flex flex-col gap-2">
          <h3 className="text-label text-brand">이것도 도와드릴까요?</h3>
          {/* 여기는 화면에 자리가 있으므로 세로로 쌓는다 (03 채팅바 위와 달리 입력창을 안 민다). */}
          <AgentPrompts
            items={state.offers.map((offer) => ({ agent: offer.agent, text: offer.label }))}
            onPick={(agent) => onPickOffer([agent])}
            layout="list"
          />
          <p className="text-caption text-ink-subtle mt-1">
            고르지 않아도 기록은 이미 남았어요. 기본값은 기록만이에요.
          </p>
        </section>
      ) : null}

      <Button variant="secondary" block onClick={onDone}>
        홈으로
      </Button>
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

function ObservationRow({ observation }: { observation: Observation }) {
  const agent = observationAgent(observation.kind);
  const detail = isHealthObservation(observation)
    ? [observation.domain_fields.symptom?.join(", "), observation.domain_fields.observed_time]
        .filter(Boolean)
        .join(", ")
    : observation.subject;

  return (
    <div className="py-3 first:pt-0 last:pb-0">
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

      <p className="text-body text-ink mt-2">{observation.raw_text}</p>

      <div className="text-caption text-ink-subtle mt-1.5 flex flex-wrap items-center gap-x-1.5">
        {detail ? <span>{detail}</span> : null}
        {detail ? <span aria-hidden>·</span> : null}
        <span>{CONFIDENCE_LABEL[observation.confidence_source]}</span>
      </div>
    </div>
  );
}
