"use client";

import { hashKey, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { josa } from "es-hangul";
import { useRef, useState } from "react";

import { STATE_LABEL } from "@/components/affinity-list";
import { CORRECTED_OBSERVATION, CorrectionButtons } from "@/components/correction-buttons";
import { DomainMeta, ObservationMeta } from "@/components/domain-chip";
import { BottomSheet } from "@/components/ui/bottom-sheet";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Spinner } from "@/components/ui/spinner";
import { phaseOf, Swap } from "@/components/ui/swap";
import { api, deleteObservation, isApiError, qk } from "@/lib/api";
import { CONFIDENCE_LABEL } from "@/lib/confidence";
import type {
  Affinity,
  AffinityState,
  CorrectionRequest,
  CorrectionResponse,
  CorrectionVerdict,
  Observation,
  ObservationDetailResponse,
  ObservationHealth,
  Ref,
} from "@/lib/api/types";
import { isHealthObservation } from "@/lib/api/types";
import { useIdempotencyKey } from "@/lib/api/use-idempotency-key";
import { formatDay, formatTimeOfDay } from "@/lib/format";
import { isMedicationRow, temperatureLabel } from "@/lib/health-observation";
import { useMotionCue } from "@/lib/motion";

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
 *
 * 🚨 **기록 지우기는 고치기가 아니다** (#736 · #779). 서버는 응답에 `cascade` 를 싣지 않고(204), 지운 기록은
 *    목록에서 빠지는 것으로 끝이 보인다 — 그래서 지우면 시트를 닫는다. 확인 띠는 승인 게이트가 아니라 확인 창이다
 *    (최상위 §2 — 보호자가 직접 누르는 삭제). 그래도 `btn-danger` 다 — 기록을 지우면 묶인 기억이 다시 계산돼
 *    그 한 건 밖까지 바뀐다 (디자인 시스템 §11 · 04 삭제 카드와 같은 이유).
 */
export type MemoryTarget =
  { type: "observation"; observation: Observation } | { type: "affinity"; affinity: Affinity };

/** 지난 교정 이력에 쓴다. 버튼 문구는 `CorrectionButtons` 의 표가 따로 든다. */
const VERDICT_LABEL: Record<CorrectionVerdict, string> = {
  once_only: "이번만 그랬어요",
  need_more_observation: "기록이 더 필요해요",
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
  /**
   * 고치기 **직전**의 기억 상태. 결과 줄이 "무엇이 바뀌었나" 를 말하려면 비교할 값이 있어야 한다.
   * 🚨 시트를 연 순간의 값이 아니다 — 한 시트에서 두 번 고치면 두 번째의 직전은 첫 번째 응답이다.
   */
  const [stateBefore, setStateBefore] = useState<AffinityState | null>(null);
  /** "기록 지우기" 를 눌러 확인 띠가 열려 있다. */
  const [deleting, setDeleting] = useState(false);
  /** 고치기 버튼 ↔ 지우기 확인 띠는 같은 자리의 **교체**다 (디자인 시스템 §8). */
  const { ref: actionsRef, cue: cueActions } = useMotionCue<HTMLDivElement>();
  /**
   * 🚨 지우기의 키. 실패 뒤 다시 누르면 같은 키다 — 서버는 이미 지운 기록을 다시 지우면 404 라, 응답만 잃은
   *    204 를 재생받으려면 키가 같아야 한다. 본문이 없어서 **기록에 묶는다** (`current(kind/id)`) —
   *    시트는 기록을 바꿔 가며 다시 열리므로, 묶지 않으면 앞 기록의 키로 다음 기록을 지운다.
   */
  const deleteKey = useIdempotencyKey();
  /** 지우는 중에 들어온 닫기 요청. 요청이 끝나면 닫는다 (아래 `requestClose`). */
  const closeWhenSettled = useRef(false);

  const ref: Ref | null =
    target === null
      ? null
      : target.type === "observation"
        ? { kind: target.observation.kind, id: target.observation.id }
        : { kind: "profile_affinity", id: target.affinity.id };

  const observation = target?.type === "observation" ? target.observation : null;
  const detail = useObservationDetail(childId, observation);
  /**
   * 기록의 **지금** 상태. 방금 고친 응답 → 다시 받은 상세 → 목록이 넘겨준 값 순으로 믿는다.
   * 🚨 목록이 넘겨준 값만 보면 시트를 연 뒤에 고친 기록(이 시트든 다른 기기든)에 버튼이 또 서고,
   *    누르면 서버가 409 `already_corrected` 를 준다 (#277). 고치기는 `active` 기록에서만 한다.
   */
  const observationStatus =
    observation === null
      ? null
      : ((result && result.target.kind !== "profile_affinity" ? result.target.status : null) ??
        detail.data?.observation.status ??
        observation.status);

  const correct = useMutation({
    mutationFn: (verdict: CorrectionVerdict) => {
      if (!ref) throw new Error("교정 대상이 없다");
      const body: CorrectionRequest = { target_ref: ref, verdict, child_id: childId };
      return api.post<CorrectionResponse>("/corrections", body);
    },
    onSuccess: (response) => {
      setStateBefore(
        target?.type !== "affinity"
          ? null
          : result?.target.kind === "profile_affinity"
            ? result.target.state
            : target.affinity.state,
      );
      setResult(response);
      // 교정 하나가 관찰·프로필·제안을 동시에 바꾼다. 아이 스코프를 통째로 무효화한다.
      void queryClient.invalidateQueries({ queryKey: qk.child(childId) });
    },
    onError: (error) => {
      // 시트를 연 뒤에 이미 고쳐진 기록이다 — 다시 받아서 고친 내용을 그린다.
      if (isApiError(error, "already_corrected")) {
        void queryClient.invalidateQueries({ queryKey: qk.child(childId) });
      }
    },
  });
  const alreadyCorrected = isApiError(correct.error, "already_corrected");

  /**
   * 지운 뒤 다시 받는다. 기록 목록 · 기억(기록 수가 다시 세진다) · 홈 기록 수 · 캘린더가 모두 아이 스코프 아래다.
   * 🚨 지운 그 기록의 상세는 다시 받지 않는다 — 서버가 404 를 주고, 닫히는 동안의 시트가 "이 기록은 제안 근거에서
   *    빠져 있어요" 로 한 번 바뀐다.
   */
  function refreshAfterDelete(target: Observation) {
    const gone = hashKey(qk.observation(childId, target.kind, target.id));
    void queryClient.invalidateQueries({
      queryKey: qk.child(childId),
      predicate: (query) => query.queryHash !== gone,
    });
  }

  const remove = useMutation({
    mutationFn: (target: Observation) =>
      deleteObservation(
        childId,
        target.kind,
        target.id,
        deleteKey.current(`${target.kind}/${target.id}`),
      ),
    onSuccess: (_response, target) => {
      deleteKey.rotate();
      refreshAfterDelete(target);
      close();
    },
    onError: (error, target) => {
      // 🚨 404 는 실패가 아니다 — 이미 지워진 기록이다 (다른 보호자가 먼저 지웠거나, 응답만 잃은 앞의 요청).
      //    목록을 다시 받아 그 줄을 뺀다. 다시 눌러도 같은 404 라 버튼은 거둔다.
      if (isApiError(error, "not_found")) refreshAfterDelete(target);
    },
    onSettled: () => {
      if (!closeWhenSettled.current) return;
      closeWhenSettled.current = false;
      close();
    },
  });

  function askDelete(open: boolean) {
    cueActions("fade");
    if (!open) remove.reset();
    setDeleting(open);
  }

  /**
   * 🚨 **지우는 중에는 닫지 않는다** (10 설정의 연결 끊기 시트와 같은 처리). `onSuccess` 는 화면이 아니라 요청에 붙어 있어
   *    닫은 뒤에도 돈다 — 그 사이 다른 기록을 열었으면 그 시트를 닫고, 같은 기록을 다시 열어 누르면 같은 키가 동시에 나간다.
   *    `dismissible={false}` 는 승인 시트 전용이라 쓰지 않는다.
   * ⚠️ 그래서 ESC 는 막지 못한다 — `<dialog>` 가 먼저 닫힌다. 그 요청을 버리면 부모의 `target` 이 남아 다음 줄을 눌러도
   *    시트가 안 열리므로(`open` 이 그대로 true), 기억해 뒀다가 요청이 끝나면 닫는다(`onSettled`). 스크림과 닫기 버튼은 그동안 막힌다.
   */
  function requestClose() {
    if (remove.isPending) {
      closeWhenSettled.current = true;
      return;
    }
    close();
  }

  function close() {
    setResult(null);
    setStateBefore(null);
    setDeleting(false);
    correct.reset();
    remove.reset();
    onClose();
  }

  return (
    <BottomSheet
      open={target !== null}
      onClose={requestClose}
      title={target?.type === "affinity" ? "기억 상세" : "기록 상세"}
      footer={
        <Button variant="secondary" block disabled={remove.isPending} onClick={requestClose}>
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

          <div ref={actionsRef}>
            {observation && deleting ? (
              <DeleteConfirm
                hasAffinity={!isHealthObservation(observation) && observation.affinity !== null}
                pending={remove.isPending}
                error={remove.error}
                onConfirm={() => remove.mutate(observation)}
                onCancel={() => askDelete(false)}
              />
            ) : observationStatus !== null && observationStatus !== "active" ? (
              // 🚨 고친 기록에는 고치기 버튼을 세우지 않는다 — 서버가 `active` 기록만 받는다 (#277).
              //    방금 이 시트에서 고쳤으면 아래 결과 줄이 같은 말을 하므로 안내도 겹쳐 세우지 않는다.
              //    🚨 **지우기는 선다** (#736) — 서버는 `deleted` 가 아닌 기록을 상태와 상관없이 지운다.
              <div className="flex flex-col gap-3">
                {result ? null : <CorrectedNote status={observationStatus} />}
                <Button
                  variant="secondary"
                  size="compact"
                  className="self-start"
                  onClick={() => askDelete(true)}
                >
                  기록 지우기
                </Button>
              </div>
            ) : alreadyCorrected ? null : (
              // 🚨 성공하면 **다시 세운다.** 확인 패널이 열린 채로 두면 방금 바꾼 것을 또 바꾸라고
              //    묻는 화면이 된다 — 실패했을 때는 `correction.id` 가 그대로라 패널이 남고,
              //    부모가 같은 자리에서 다시 누를 수 있다 (그게 재시도다).
              //    🚨 기억은 몇 번이든 다시 고칠 수 있다(서버가 막지 않는다) — 지우기는 기록에만 있다.
              <CorrectionButtons
                key={result?.correction.id ?? "new"}
                targetKind={target.type}
                onSelect={(verdict) => correct.mutate(verdict)}
                pending={correct.isPending ? (correct.variables ?? null) : null}
                onDelete={observation ? () => askDelete(true) : undefined}
                // 🚨 건강 기록은 판정 없이 지우기만 선다 (#764) — 서버가 건강 기록 고치기를 400 으로 막는다.
                //    "이번만 그랬어요" 는 기억 집계에서 빼는 일인데 건강 기록은 기억에 묶이지 않아서다.
                deleteOnly={observation !== null && isHealthObservation(observation)}
              />
            )}
          </div>

          {/* 🚨 실패를 빨강으로 칠하지 않는다 (문서 §3). 409 는 실패가 아니라 이미 끝난 일이라
              "다시 눌러주세요" 를 쓰지 않는다 — 다시 눌러도 같은 409 다. */}
          {alreadyCorrected ? (
            <p className="text-body-sm text-ink-muted" role="status">
              이미 고친 기록이에요.
            </p>
          ) : correct.isError ? (
            <p className="text-body-sm text-ink-muted" role="status">
              지금은 고치지 못했어요. 잠시 뒤에 다시 눌러주세요.
            </p>
          ) : null}

          {result ? (
            <CascadeResult result={result} targetKind={target.type} stateBefore={stateBefore} />
          ) : null}
        </div>
      )}
    </BottomSheet>
  );
}

/**
 * 🚨 무엇이 다시 계산됐는지는 **서버가 준 숫자**로만 말한다. "추천이 바뀔 거예요" 같은
 *    예측을 프론트가 쓰지 않는다 — 재계산 범위를 아는 것은 Curator 뿐이다.
 *
 * 🚨 **기억을 고쳤으면 응답의 상태를 직전 상태와 비교해 말한다.** 기억 고치기는 의견이라
 *    (`CorrectionButtons` 의 🚨) 상태가 그대로인 경우가 흔한데, "다시 계산했어요" 만 쓰면
 *    무엇이 됐는지 알 수 없다. 그대로면 **그대로라고** 말하고, 이유는 서버의 `state_reason` 이 진다.
 *    "기억 1건을 다시 계산했어요" 는 쓰지 않는다 — 고친 그 기억 자신이라 아무것도 알려 주지 않는다.
 */
function CascadeResult({
  result,
  targetKind,
  stateBefore,
}: {
  result: CorrectionResponse;
  /** 🚨 관찰과 프로필을 "기억" 한 단어로 묶지 않는다 (`CorrectionButtons` 의 🚨). */
  targetKind: "observation" | "affinity";
  /** 기억을 고쳤을 때만 있다. 고치기 직전의 상태. */
  stateBefore: AffinityState | null;
}) {
  const affinity = result.target.kind === "profile_affinity" ? result.target : null;
  const profiles = affinity ? 0 : result.cascade.affinities_recomputed.length;
  const suggestions = result.cascade.suggestions_recalculated.length;

  return (
    <div className="bg-surface-muted rounded-field p-3.5" role="status">
      <p className="text-body-sm text-ink">
        {/* 🚨 라벨에 조사를 붙여 말하지 않는다 — "기록이 더 필요해요로 반영했어요" 가 됐다 (#815).
            ⚠️ 기록 쪽 문장은 기록 교정이 `once_only` 하나뿐이라는 전제다 (`CorrectionButtons` 의 `VERDICTS.observation`).
               판정이 늘면 `result.correction.verdict` 로 가른다. */}
        {affinity ? "남긴 의견을 반영했어요." : "이번 한 번만 있던 일로 바꿨어요."}
      </p>
      {affinity && stateBefore ? (
        <div className="mt-1">
          <p className="text-caption text-ink-muted">
            {stateBefore === affinity.state
              ? "기억의 상태는 바뀌지 않았어요."
              : `기억이 ${STATE_LABEL[stateBefore]}에서 ${josa(STATE_LABEL[affinity.state], "으로/로")} 바뀌었어요.`}
          </p>
          <p className="text-caption text-ink-muted">{affinity.state_reason}</p>
        </div>
      ) : null}
      {affinity && suggestions === 0 ? null : profiles === 0 && suggestions === 0 ? (
        <p className="text-caption text-ink-muted mt-1">다시 계산된 것은 없어요.</p>
      ) : (
        <ul className="text-caption text-ink-muted mt-1 flex flex-col gap-0.5">
          {profiles > 0 ? <li>기억 {profiles}건을 다시 계산했어요.</li> : null}
          {suggestions > 0 ? (
            <li>
              {targetKind === "observation" ? "이 기록" : "이 기억"}을 쓰던 제안 {suggestions}건이
              달라졌어요.
            </li>
          ) : null}
        </ul>
      )}
    </div>
  );
}

/**
 * 기록 상세 조회. 시트 본체(지금 상태 — 고치기 버튼을 세울지)와 `ObservationDetail`(근거 · 이력)이
 * **같은 키**로 같이 쓴다 — TanStack Query 가 한 번만 부른다.
 */
function useObservationDetail(childId: string, observation: Observation | null) {
  return useQuery({
    queryKey: observation
      ? qk.observation(childId, observation.kind, observation.id)
      : qk.observation(childId, "", ""),
    queryFn: () =>
      api.get<ObservationDetailResponse>(
        `/children/${childId}/observations/${observation!.kind}/${observation!.id}`,
      ),
    enabled: observation !== null,
  });
}

/**
 * 기록 지우기 확인 띠 (#736 · #779). 고치기의 확인 패널과 같은 자리 · 같은 모양이다.
 *
 * 🚨 **일어날 일만 적는다.** 목록에서 빠지고 제안의 근거로 쓰이지 않는 것은 서버가 지키는 사실이다
 *    (`deleted` 를 목록 · 근거 · 검색이 모두 뺀다). 기억은 **묶인 기억이 있을 때만** 말한다 — 생활 기록처럼
 *    묶인 기억이 없는 기록에 "다시 계산돼요" 를 쓰면 없는 일을 약속한다.
 * 🚨 `btn-approve` 도 `caution` 도 쓰지 않는다 — 승인 게이트가 아니다 (최상위 §2).
 * 🚨 실패를 빨강으로 칠하지 않는다. "기록은 그대로" 라고 단정하지 않는다 — 응답만 잃었으면 서버는 이미 지웠다.
 */
function DeleteConfirm({
  hasAffinity,
  pending,
  error,
  onConfirm,
  onCancel,
}: {
  hasAffinity: boolean;
  pending: boolean;
  error: Error | null;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  // 🚨 다시 눌러도 같은 응답이 오는 실패다 — 버튼을 잠근다 (04 삭제 카드의 `stuckBy` 와 같다).
  const denied = isApiError(error, "child_access_denied");

  if (isApiError(error, "not_found")) {
    return (
      <div className="border-line rounded-card bg-surface-muted border p-4" role="status">
        <p className="text-body text-ink">이미 지워진 기록이에요.</p>
        <p className="text-body-sm text-ink-muted mt-1">목록에서도 빠져요.</p>
      </div>
    );
  }

  return (
    <div className="border-line rounded-card bg-surface-muted border p-4">
      <div role="status">
        <p className="text-body text-ink">이 기록을 지울까요?</p>
        <p className="text-body-sm text-ink-muted mt-1">
          {hasAffinity
            ? "목록에서 빠지고 제안의 근거로도 쓰이지 않아요. 묶인 기억은 다시 계산돼요."
            : "목록에서 빠지고 제안의 근거로도 쓰이지 않아요."}
        </p>
        <p className="text-caption text-ink-subtle mt-2">지운 기록은 되살릴 수 없어요.</p>
      </div>

      {error ? (
        <p className="text-body-sm text-ink-muted mt-3" role="status">
          {denied
            ? "이 아이의 기록을 지금은 지울 수 없어요."
            : "지금은 지우지 못했어요. 잠시 뒤에 다시 눌러주세요."}
        </p>
      ) : null}

      <div className="mt-3 flex gap-2">
        <Button
          variant="danger"
          size="compact"
          className="flex-1"
          disabled={pending || denied}
          onClick={onConfirm}
        >
          {pending ? <Spinner /> : null}
          {pending ? "지우는 중이에요" : "지우기"}
        </Button>
        <Button variant="tertiary" size="compact" disabled={pending} onClick={onCancel}>
          그만두기
        </Button>
      </div>
    </div>
  );
}

/** 이미 고친 기록을 열었을 때 버튼 자리에 서는 한 덩이. 무엇으로 고쳤고 그래서 어떻게 되는지. */
function CorrectedNote({ status }: { status: "stand_alone" | "inactive" }) {
  const { label, note } = CORRECTED_OBSERVATION[status];
  return (
    <div className="bg-surface-muted rounded-field p-3.5">
      <p className="text-body-sm text-ink">
        ‘{label}’{josa.pick(label, "으로/로")} 고친 기록이에요.
      </p>
      <p className="text-caption text-ink-muted mt-1">{note}</p>
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
  const detail = useObservationDetail(childId, observation);
  // 목록이 넘겨준 값보다 다시 받은 상세가 새것이다 — 이 시트에서 방금 고친 기록도 여기서 갈린다.
  const currentStatus = detail.data?.observation.status ?? observation.status;

  return (
    <div className="flex flex-col gap-4">
      {/* 화면의 주인공은 결과가 아니라 부모가 적은 말이다 (문서 §11 · 04 와 같은 규칙). */}
      <div className="rounded-card border-brand bg-surface border p-4">
        <p className="text-body text-ink">{observation.raw_text}</p>
      </div>

      <dl className="flex flex-col gap-2">
        <MetaRow label="분류">
          <ObservationMeta kind={observation.kind} />
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
          // 🚨 고친 기록도 서버가 `affinity` 를 그대로 싣는다 — 고치기는 `status` 만 바꾸고 연결은
          //    남긴다. 다만 그 기억의 기록 수에는 `active` 만 들어가서, 고친 기록에 "관련된 기억" 이라고
          //    쓰면 여전히 센다고 읽힌다. 사실은 버리지 않고 라벨로 말한다 (목록은 칩을 아예 뺀다).
          <MetaRow label={currentStatus === "active" ? "관련된 기억" : "세지 않는 기억"}>
            {observation.affinity.merge_key}
          </MetaRow>
        ) : null}
      </dl>

      <section>
        <h3 className="text-section text-ink">이 기록을 쓴 제안</h3>
        {/* 스켈레톤 → 내용은 같은 자리의 교체다 (디자인 시스템 §8). */}
        <Swap id={phaseOf(detail)}>
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
          ) : isHealthObservation(observation) ? (
            // 🚨 건강 기록은 고칠 수 없어서 근거에서 "빠질" 일이 없다 (#764). 빠졌다고 말하면 보호자가 무언가를
            //    해서 뺀 것처럼 읽힌다 — 아직 쓴 추천이 없다는 사실만 말한다.
            <p className="text-body-sm text-ink-muted mt-2">아직 이 기록을 쓴 추천이 없어요.</p>
          ) : (
            <p className="text-body-sm text-ink-muted mt-2">아직 이 기록을 쓴 제안이 없어요.</p>
          )}
        </Swap>
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
 *    체온도 숫자와 잰 부위만 옮긴다 — 정상 범위 · 높다/낮다 · 색이 없다.
 * 🚨 한 행 = 한 사건이다 (#551). 먹인 약 행은 증상이 비어 있고 `observed_time` 이 먹인 시각이다.
 */
function HealthFields({ fields }: { fields: ObservationHealth["domain_fields"] }) {
  const symptoms = fields.symptom;
  const medicine = isMedicationRow(fields);
  const bodyPart = typeof fields.body_part === "string" ? fields.body_part : null;
  const temperature = temperatureLabel(fields);
  const measuredAt =
    typeof fields.measured_at === "string" ? formatTimeOfDay(fields.measured_at) : null;
  const when =
    typeof fields.observed_time === "string" ? formatTimeOfDay(fields.observed_time) : null;

  return (
    <>
      {symptoms && symptoms.length > 0 ? (
        <MetaRow label="증상">{symptoms.join(", ")}</MetaRow>
      ) : null}
      {medicine ? <MetaRow label="먹인 약">{fields.medication_given}</MetaRow> : null}
      {temperature ? <MetaRow label="체온">{temperature}</MetaRow> : null}
      {measuredAt ? <MetaRow label="잰 시간">{measuredAt}</MetaRow> : null}
      {bodyPart ? <MetaRow label="부위">{bodyPart}</MetaRow> : null}
      {when ? <MetaRow label={medicine ? "먹인 시간" : "시간"}>{when}</MetaRow> : null}
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
        <MetaRow label="쌓인 기록">{affinity.observation_count}건</MetaRow>
        <MetaRow label="마지막 기록">{formatDay(affinity.last_observed_on)}</MetaRow>
      </dl>

      {/* 🚨 **없는 버튼을 가리키지 않는다.** 예전엔 "맞아요를 눌러주세요" 였는데 `confirm` 은 화면에서
          묻지 않는다 (최상위 §5 Correction · `correction-buttons.tsx`). 오래된 기억을 되살리는 것은
          새 기록이다 — 6개월 규칙이 보는 것이 마지막 기록이 언제인가라서 (#242).
          "프로필" 이 아니라 "기억" 이다 — 탭 이름과 카드 문구가 그렇게 부른다. */}
      {affinity.is_stale ? (
        <p className="text-body-sm text-ink-muted">
          6개월이 지나서 이 기억만으로는 제안하지 않아요. 요즘도 그렇다면 홈에서 한 줄 적어 주세요.
          새로 기록되면 다시 제안에 써요.
        </p>
      ) : null}
    </div>
  );
}

function MetaRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-start gap-3">
      {/* 라벨의 줄 높이를 값(`text-body-sm`)의 줄 높이에 맞춘다. 글자가 작은 라벨이 위로 뜨지 않고,
          값이 여러 줄이어도 라벨은 첫 줄에 붙는다 (`items-center` 면 가운데로 내려간다). */}
      <dt className="text-label text-ink-subtle w-20 shrink-0 leading-[calc(var(--text-body-sm)*var(--text-body-sm--line-height))]">
        {label}
      </dt>
      <dd className="text-body-sm text-ink flex min-w-0 flex-1 items-center">{children}</dd>
    </div>
  );
}
