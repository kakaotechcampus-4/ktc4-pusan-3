"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { CorrectionButtons } from "@/components/correction-buttons";
import { DomainMeta, observationAgent } from "@/components/domain-chip";
import { BottomSheet } from "@/components/ui/bottom-sheet";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { api, qk } from "@/lib/api";
import type {
  Affinity,
  ConfidenceSource,
  CorrectionRequest,
  CorrectionResponse,
  CorrectionVerdict,
  Observation,
  ObservationDetailResponse,
  Ref,
} from "@/lib/api/types";
import { isHealthObservation } from "@/lib/api/types";
import { formatDay } from "@/lib/format";

/**
 * 07 상세 · 교정 시트 — 관찰과 프로필이 **같은 시트**를 쓴다.
 *
 * 목록은 둘을 다른 모양으로 그리지만(줄 / 카드) 고치는 동작은 같은 엔드포인트 하나다
 * (`POST /corrections` · `target_ref` 만 다르다). 시트를 둘로 나누면 4버튼 문구와
 * cascade 처리가 두 벌이 되고, 언젠가 한쪽만 고쳐진다.
 *
 * 🚨 **승인 시트가 아니다.** `dismissible` 를 막지 않고 `caution` · `btn-approve` 를 쓰지
 *    않는다 — 그 둘은 되돌릴 수 없는 2곳 전용이다 (CLAUDE.md §2). 교정은 되돌릴 수 있다.
 *
 * 🚨 **고친 뒤 시트를 자동으로 닫지 않는다.** 무엇이 다시 계산됐는지(`cascade`)를 보여주는
 *    자리가 여기뿐이라, 닫아 버리면 부모는 자기가 뭘 바꿨는지 모른 채로 목록으로 돌아간다.
 */
export type MemoryTarget =
  { type: "observation"; observation: Observation } | { type: "affinity"; affinity: Affinity };

/** 무엇을 보고 저장한 관찰인가. 서버 enum 을 화면 말로 옮기는 표다. */
const CONFIDENCE_LABEL: Record<ConfidenceSource, string> = {
  institution_notice: "기관 공지에서",
  parent_direct: "보호자가 직접 확인",
  parent_hedged: "보호자가 조심스럽게",
  parent_hearsay: "전해 들은 말",
};

const VERDICT_LABEL: Record<CorrectionVerdict, string> = {
  confirm: "맞아요",
  once_only: "한 번 본 것뿐",
  outdated: "지금은 달라요",
  wrong: "잘못된 기록",
};

export function MemoryDetailSheet({
  childId,
  target,
  onClose,
}: {
  childId: string;
  /** `null` 이면 닫힌 상태다. */
  target: MemoryTarget | null;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [result, setResult] = useState<CorrectionResponse | null>(null);

  const ref: Ref | null =
    target === null
      ? null
      : target.type === "observation"
        ? { kind: target.observation.kind, id: target.observation.id }
        : { kind: "profile_affinity", id: target.affinity.id };

  const correct = useMutation({
    mutationFn: (verdict: CorrectionVerdict) => {
      if (!ref) throw new Error("교정 대상이 없다");
      const body: CorrectionRequest = { target_ref: ref, verdict, child_id: childId };
      return api.post<CorrectionResponse>("/corrections", body);
    },
    onSuccess: (response) => {
      setResult(response);
      // 교정 하나가 관찰·프로필·제안을 동시에 바꾼다. 아이 스코프를 통째로 무효화한다.
      void queryClient.invalidateQueries({ queryKey: qk.child(childId) });
    },
  });

  function close() {
    setResult(null);
    correct.reset();
    onClose();
  }

  return (
    <BottomSheet
      open={target !== null}
      onClose={close}
      title={target?.type === "affinity" ? "프로필 상세" : "기억 상세"}
      footer={
        <Button variant="secondary" block onClick={close}>
          닫기
        </Button>
      }
    >
      {target === null ? null : (
        <div className="flex flex-col gap-5">
          {target.type === "observation" ? (
            <ObservationDetail childId={childId} observation={target.observation} />
          ) : (
            <AffinityDetail affinity={target.affinity} />
          )}

          <CorrectionButtons
            onSelect={(verdict) => correct.mutate(verdict)}
            pending={correct.isPending ? (correct.variables ?? null) : null}
          />

          {/* 🚨 실패를 빨강으로 칠하지 않는다 (문서 §3). */}
          {correct.isError ? (
            <p className="text-body-sm text-ink-muted" role="status">
              지금은 고치지 못했어요. 잠시 뒤에 다시 눌러주세요.
            </p>
          ) : null}

          {result ? <CascadeResult result={result} /> : null}
        </div>
      )}
    </BottomSheet>
  );
}

/**
 * 🚨 무엇이 다시 계산됐는지는 **서버가 준 숫자**로만 말한다. "추천이 바뀔 거예요" 같은
 *    예측을 프론트가 쓰지 않는다 — 재계산 범위를 아는 것은 Curator 뿐이다.
 */
function CascadeResult({ result }: { result: CorrectionResponse }) {
  const profiles = result.cascade.affinities_recomputed.length;
  const suggestions = result.cascade.suggestions_recalculated.length;

  return (
    <div className="bg-surface-muted rounded-field p-3.5" role="status">
      <p className="text-body-sm text-ink">
        {VERDICT_LABEL[result.correction.verdict]}로 반영했어요.
      </p>
      {profiles === 0 && suggestions === 0 ? (
        <p className="text-caption text-ink-muted mt-1">다시 계산된 것은 없어요.</p>
      ) : (
        <ul className="text-caption text-ink-muted mt-1 flex flex-col gap-0.5">
          {profiles > 0 ? <li>프로필 {profiles}건을 다시 계산했어요.</li> : null}
          {suggestions > 0 ? <li>이 기억을 쓰던 추천 {suggestions}건이 달라졌어요.</li> : null}
        </ul>
      )}
    </div>
  );
}

function ObservationDetail({
  childId,
  observation,
}: {
  childId: string;
  observation: Observation;
}) {
  /**
   * 목록이 이미 관찰 본문을 들고 있는데도 상세를 부르는 이유는 **`used_in` 과 교정 이력** 때문이다.
   * 목록 응답에는 없고, "이 기억은 제안 근거에서 빠져 있어요" 를 그리려면 그 값이 있어야 한다.
   */
  const detail = useQuery({
    queryKey: qk.observation(childId, observation.kind, observation.id),
    queryFn: () =>
      api.get<ObservationDetailResponse>(
        `/children/${childId}/observations/${observation.kind}/${observation.id}`,
      ),
  });

  return (
    <div className="flex flex-col gap-4">
      {/* 화면의 주인공은 결과가 아니라 부모가 적은 말이다 (문서 §11 · 04 와 같은 규칙). */}
      <div className="rounded-card border-brand bg-surface border p-4">
        <p className="text-body text-ink">{observation.raw_text}</p>
      </div>

      <dl className="flex flex-col gap-2">
        <MetaRow label="분류">
          <DomainMeta agent={observationAgent(observation.kind)} />
        </MetaRow>
        {observation.observed_label ? (
          <MetaRow label="언제">{observation.observed_label}</MetaRow>
        ) : null}
        <MetaRow label="출처">{CONFIDENCE_LABEL[observation.confidence_source]}</MetaRow>
        {observation.source_writer ? (
          <MetaRow label="적은 사람">{observation.source_writer.nickname}</MetaRow>
        ) : null}
        {isHealthObservation(observation) ? (
          <HealthFields fields={observation.domain_fields} />
        ) : observation.affinity ? (
          <MetaRow label="묶인 프로필">{observation.affinity.merge_key}</MetaRow>
        ) : null}
      </dl>

      <section>
        <h3 className="text-section text-ink">이 기억을 쓴 추천</h3>
        {detail.isPending ? (
          <Skeleton className="mt-2 h-12 w-full" />
        ) : detail.data && detail.data.used_in.length > 0 ? (
          <ul className="mt-2 flex flex-col gap-2">
            {detail.data.used_in.map((used) => (
              <li key={used.suggestion_id} className="text-body-sm text-ink-muted">
                {used.content}
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-body-sm text-ink-muted mt-2">이 기억은 제안 근거에서 빠져 있어요.</p>
        )}
      </section>

      {detail.data && detail.data.corrections.length > 0 ? (
        <section>
          <h3 className="text-section text-ink">지금까지 고친 기록</h3>
          <ul className="mt-2 flex flex-col gap-1">
            {detail.data.corrections.map((correction) => (
              <li key={correction.id} className="text-caption text-ink-subtle">
                {VERDICT_LABEL[correction.verdict]} · {formatDay(correction.created_at)}
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </div>
  );
}

/**
 * 🚨 health 는 모양이 다르다 — `subject` · `polarity` · `affinity` 키가 아예 없고
 *    `symptom` 은 **배열**이다 (apps/web/CLAUDE.md §4).
 * 🚨 여기 있는 값은 전부 보호자가 적은 것을 서버가 담은 것이다. 화면이 증상을 해석하거나
 *    심각도를 판단하지 않는다 — Health Agent 도 진단하지 않는다 (CLAUDE.md §2).
 */
function HealthFields({ fields }: { fields: { symptom?: string[]; [key: string]: unknown } }) {
  const symptoms = fields.symptom;
  const bodyPart = typeof fields.body_part === "string" ? fields.body_part : null;
  const when = typeof fields.observed_time === "string" ? fields.observed_time : null;

  return (
    <>
      {symptoms && symptoms.length > 0 ? (
        <MetaRow label="증상">{symptoms.join(", ")}</MetaRow>
      ) : null}
      {bodyPart ? <MetaRow label="부위">{bodyPart}</MetaRow> : null}
      {when ? <MetaRow label="시간">{when}</MetaRow> : null}
    </>
  );
}

function AffinityDetail({ affinity }: { affinity: Affinity }) {
  return (
    <div className="flex flex-col gap-4">
      <div className="rounded-card border-brand bg-surface border p-4">
        <p className="text-title text-ink">{affinity.merge_key}</p>
        <p className="text-body-sm text-ink-muted mt-1">{affinity.state_reason}</p>
      </div>

      <dl className="flex flex-col gap-2">
        <MetaRow label="분류">
          <DomainMeta agent={affinity.domain} />
        </MetaRow>
        <MetaRow label="쌓인 관찰">{affinity.observation_count}건</MetaRow>
        <MetaRow label="마지막 관찰">{formatDay(affinity.last_observed_on)}</MetaRow>
      </dl>

      {affinity.is_stale ? (
        <p className="text-body-sm text-ink-muted">
          6개월이 지나서 이 프로필만으로는 추천을 만들지 않아요. 요즘도 그렇다면 맞아요를
          눌러주세요.
        </p>
      ) : null}
    </div>
  );
}

function MetaRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-start gap-3">
      <dt className="text-label text-ink-subtle w-20 shrink-0">{label}</dt>
      <dd className="text-body-sm text-ink flex min-w-0 flex-1 items-center">{children}</dd>
    </div>
  );
}
