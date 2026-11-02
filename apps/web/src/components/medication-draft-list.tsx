"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { useState } from "react";

import {
  MedicationDraftCard,
  type MedicationSubmitState,
} from "@/components/medication-draft-card";
import { Button, ButtonLink } from "@/components/ui/button";
import { ICON_SIZE, ICON_STROKE } from "@/components/ui/icon";
import { qk, submitMedicationDraft, updateMedicationDraft, type MedicationDraft } from "@/lib/api";
import { useIdempotencyKey } from "@/lib/api/use-idempotency-key";
import { failureOf, submitBodyOf, type MedicationFields } from "@/lib/medication";
import {
  NO_MEDICATION_DRAFTS,
  mergeMedicationDrafts,
  useMedicationDraftStore,
} from "@/stores/medication-draft";

/**
 * 복약 초안 묶음 — 04 대화의 "찾은 복약" 시트가 담는다 (#537 · `docs/web/medication-v1.md` §2).
 *
 * 🚨 **제출은 건별이다** — 카드마다 자기 버튼 · 자기 키 · 자기 결과다 (일정 초안과 같다 · 9/21).
 * 🚨 **여러 장이면 한 장씩 넘긴다.** 카드가 시각 · 기간 · 선택 칸 · 제출까지 든 긴 폼이라 쌓으면 화면 서너 폭이
 *    되고, `btn-approve` 가 한 화면에 여럿 선다 (`EventDraftList` 머리말과 같은 이유).
 * 🚨 **제출해도 다음 장이 저절로 열리지 않는다.** 넣은 카드는 "넣었어요" 로 그 자리에 남고, 넣은 건수를 말한다.
 *
 * 🚨 **그리는 목록의 정본은 여기다.** 스토어에서 바로 그리면 제출한 카드가 그 순간 사라져 "넣었어요" 를 볼 자리가
 *    없다. 스토어는 다음에 열었을 때 남은 것을, 이 배열은 지금 화면에 선 것을 진다.
 */
export function MedicationDraftList({
  childId,
  draftIds,
}: {
  childId: string;
  /** 이 답(run)이 낸 초안만 그린다 — 대화에는 run 이 여럿이다. */
  draftIds: string[];
}) {
  const stored = useMedicationDraftStore((s) => s.byChild[childId] ?? NO_MEDICATION_DRAFTS);
  const [rows] = useState<MedicationDraft[]>(() =>
    mergeMedicationDrafts(
      [],
      stored.filter((d) => draftIds.includes(d.draft_id)),
    ),
  );
  const [page, setPage] = useState(0);
  const [submittedIds, setSubmittedIds] = useState<string[]>([]);

  if (rows.length === 0) return null;

  const index = Math.min(page, rows.length - 1);
  const done = submittedIds.length;
  const multiple = rows.length > 1;

  return (
    <section className="flex flex-col gap-3">
      {/* 🚨 "저장했어요" 와 같은 머리글을 쓰지 않는다 — 넣어야 들어가는 것이다. */}
      <div>
        <h3 className="text-label text-brand">복약 일정으로 넣을까요?</h3>
        <p className="text-body-sm text-ink-muted mt-1">
          적어주신 말에서 복약을 찾았어요. 넣기 전까지는 등록되지 않아요.
        </p>
      </div>

      {/* 🚨 **모든 장을 마운트해 두고 지금 장만 보인다.** 지금 장만 그리면 넘길 때 앞 장이 언마운트되면서 제출 상태와
          키 홀더가 같이 사라진다 — 넣은 장으로 돌아오면 "아직 저장되지 않았어요" 로 되살아나 **새 키로 한 번 더**
          넣을 수 있었다(같은 약이 코스 둘). 결과를 확인하지 못한 제출 뒤 다시 누르는 것도 같은 키여야 한다 (게이트 ㉡).
          `key` 는 `draft_id` 다 — 장마다 자기 칸 값 · 자기 키를 든다. */}
      {rows.map((draft, i) => (
        <div key={draft.draft_id} hidden={i !== index}>
          <MedicationDraftRow
            childId={childId}
            draft={draft}
            onSubmitted={(draftId) =>
              setSubmittedIds((prev) => (prev.includes(draftId) ? prev : [...prev, draftId]))
            }
          />
        </div>
      ))}

      {multiple ? (
        <div className="flex flex-col gap-2">
          <div className="flex items-center justify-between gap-2">
            <Button
              variant="tertiary"
              size="compact"
              disabled={index === 0}
              onClick={() => setPage(index - 1)}
            >
              <ChevronLeft aria-hidden size={ICON_SIZE.sm} strokeWidth={ICON_STROKE} />
              이전
            </Button>
            <p aria-live="polite" className="text-body-sm text-ink-muted">
              {rows.length}건 중 {index + 1}번째
            </p>
            <Button
              variant="tertiary"
              size="compact"
              disabled={index === rows.length - 1}
              onClick={() => setPage(index + 1)}
            >
              다음
              <ChevronRight aria-hidden size={ICON_SIZE.sm} strokeWidth={ICON_STROKE} />
            </Button>
          </div>
          {done > 0 ? (
            <p role="status" className="text-body-sm text-ink-muted text-center">
              {done === rows.length
                ? `${rows.length}건 전부 넣었어요.`
                : `${rows.length}건 중 ${done}건 넣었어요.`}
            </p>
          ) : null}
        </div>
      ) : null}

      {/* 회차 체크는 복약 화면에서만 한다 — 넣은 뒤 갈 곳을 같은 자리에 둔다. */}
      {done > 0 ? (
        <ButtonLink
          href={`/child/${childId}/profile/medications`}
          variant="secondary"
          size="compact"
        >
          복약 화면에서 보기
        </ButtonLink>
      ) : null}
    </section>
  );
}

/**
 * 초안 한 장의 제출. 🚨 **카드마다 이 컴포넌트가 하나씩**이라 `useIdempotencyKey()` 도 하나씩이다 —
 * 하나로 묶으면 두 번째 제출이 "같은 키 · 다른 본문" 이라 422 다.
 */
function MedicationDraftRow({
  childId,
  draft,
  onSubmitted,
}: {
  childId: string;
  draft: MedicationDraft;
  onSubmitted: (draftId: string) => void;
}) {
  const queryClient = useQueryClient();
  const remove = useMedicationDraftStore((s) => s.remove);
  const submitKey = useIdempotencyKey();
  const [submitted, setSubmitted] = useState(false);

  const submit = useMutation({
    mutationFn: (fields: MedicationFields) => {
      const body = submitBodyOf(draft, fields);
      // 카드가 잠근 상태에서는 부르지 않는다 — 여기 오면 화면 버그다. 요청을 내보내지 않는다.
      if (!body) return Promise.reject(new Error("비어 있는 칸이 있어서 넣지 않았어요."));
      // 🚨 키는 **본문에 묶는다** — 실패 뒤 칸을 고쳐 다시 누르면 다른 본문이라 새 키여야 422 에 안 갇힌다.
      const key = submitKey.current(JSON.stringify(body));
      // 🚨 `op` 에 따라 엔드포인트가 갈린다 — create 는 POST, update 는 PATCH. 둘 다 게이트 ㉡ 이고 키를 단다.
      if (draft.op === "update" && draft.schedule_id) {
        const { schedule, doses } = body;
        return updateMedicationDraft(childId, draft.schedule_id, { schedule, doses }, key);
      }
      return submitMedicationDraft(childId, body, key);
    },
    onSuccess: async () => {
      // 🚨 성공한 뒤에만 다음 키로 넘어간다. 실패 뒤 다시 누르는 것은 재시도라 같은 키여야 한다.
      submitKey.rotate();
      setSubmitted(true);
      onSubmitted(draft.draft_id);
      // 🚨 스토어에서 뺀다 — 다시 열었을 때 이미 넣은 복약이 초안으로 되살아나지 않게.
      remove(childId, draft.draft_id);
      // 🚨 낙관적 업데이트를 쓰지 않는다 (게이트). 응답을 받은 뒤 아이 스코프를 무효화한다 — 복약 화면이 다시 받는다.
      await queryClient.invalidateQueries({ queryKey: qk.child(childId) });
    },
  });

  const state: MedicationSubmitState = submitted
    ? "submitted"
    : submit.isPending
      ? "submitting"
      : submit.isError
        ? "failed"
        : "idle";

  const failure = submit.isError ? failureOf(submit.error) : null;

  return (
    <MedicationDraftCard
      draft={draft}
      state={state}
      error={failure?.text}
      rejected={failure?.final ?? false}
      onSubmit={(fields) => submit.mutate(fields)}
    />
  );
}
