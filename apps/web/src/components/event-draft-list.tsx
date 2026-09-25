"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import { EventDraftCard, type DraftSubmitState } from "@/components/event-draft-card";
import {
  qk,
  submitEventDraft,
  updateEventDraft,
  type EventDraft,
  type EventDraftFields,
  type EventDraftItem,
} from "@/lib/api";
import { useIdempotencyKey } from "@/lib/api/use-idempotency-key";
import { useEventDraftStore } from "@/stores/event-draft";

/**
 * 일정 초안 묶음 — **04 저장 결과와 08 사진이 같이 쓴다.**
 *
 * 🚨 **제출은 건별이다** (9/21 회의). 카드마다 자기 버튼·자기 키·자기 결과를 갖는다.
 *    한 요청으로 묶지 않으므로 3장 중 1장이 실패해도 나머지 2장은 이미 들어가 있다 (NF-06).
 *
 * 🚨 **초안은 서버에 없다.** 들어온 묶음을 세션 스토리지에 넣고(`stores/event-draft.ts`),
 *    그 스토어가 같은 `event_id` 를 최신 한 장으로 줄인다 — 낡은 초안이 뒤 결과를 지우는 것을
 *    막는 1차 방어다 (#122).
 *
 * 🚨 **넣은 초안은 스토어에서 뺀다.** 안 빼면 다음에 이 화면을 열 때 이미 캘린더에 있는 일정이
 *    "아직 저장되지 않았어요" 로 다시 뜬다. 다만 **화면에서는 그 자리에 남겨** 무엇이 들어갔는지
 *    보이게 한다 — 그래서 그리는 목록(`rows`)과 남겨 두는 스토어가 다른 값이다.
 */
export function EventDraftList({
  childId,
  /** 이번 run 이 내보낸 묶음. 🚨 스토어에 얹는 것은 **처음 보는 것만**이다 (아래 `seen`). */
  incoming,
  found,
}: {
  childId: string;
  incoming: EventDraft[];
  /**
   * 어디서 찾은 일정인지 한 줄. 🚨 **그릇이 넘긴다 — 목록이 지어내지 않는다.**
   *    여기 "적어주신 말에서 찾았어요" 를 박아 뒀더니 08 사진 화면에도 그대로 떴다.
   *    카드의 `hint` 에서 낸 것과 같은 사고라, 출처를 아는 쪽이 말하게 한다.
   */
  found: string;
}) {
  const addDrafts = useEventDraftStore((s) => s.addDrafts);
  const storeDrafts = useEventDraftStore((s) => s.byChild[childId]);

  /**
   * 🚨 **그리는 목록의 정본은 여기다.** 스토어에서 바로 그리면 제출한 카드가 그 순간 사라져서
   *    "넣었어요" 를 볼 자리가 없다. 스토어는 **다음에 다시 열었을 때 뭐가 남아 있는가**를 지고,
   *    이 배열은 **지금 화면에 뭐가 서 있는가**를 진다.
   */
  const [rows, setRows] = useState<EventDraft[]>(() => storeDrafts ?? []);

  /**
   * 🚨 이미 얹은 초안을 다시 얹지 않는다. 제출하고 스토어에서 뺀 장이 effect 가 다시 돌 때
   *    되살아나는 것을 막는다 — 그러면 이미 캘린더에 넣은 일정이 초안으로 부활한다.
   */
  const seen = useRef(new Set<string>());

  useEffect(() => {
    const fresh = incoming.filter((d) => !seen.current.has(d.draft_id));
    if (fresh.length === 0) return;
    for (const d of fresh) seen.current.add(d.draft_id);
    addDrafts(childId, fresh);
    setRows((prev) => {
      const ids = new Set(fresh.map((d) => d.draft_id));
      return [...prev.filter((d) => !ids.has(d.draft_id)), ...fresh];
    });
  }, [incoming, childId, addDrafts]);

  if (rows.length === 0) return null;

  return (
    <section className="flex flex-col gap-3">
      {/* 🚨 **"저장했어요" 와 같은 머리글을 쓰지 않는다.** 위의 관찰·기억은 이미 저장된 것이고
          여기는 보호자가 넣어야 들어가는 것이다 — 머리글이 그 선을 긋는다. */}
      <div>
        <h3 className="text-label text-brand">일정으로 만들까요?</h3>
        <p className="text-body-sm text-ink-muted mt-1">
          {found} 넣기 전까지는 캘린더에 들어가지 않아요.
        </p>
      </div>

      {rows.map((draft) => (
        <DraftRow key={draft.draft_id} childId={childId} draft={draft} />
      ))}
    </section>
  );
}

/**
 * 초안 한 장의 제출. 🚨 **카드마다 이 컴포넌트가 하나씩**이라 `useIdempotencyKey()` 도 하나씩이다 —
 * 하나로 묶으면 두 번째 제출이 "같은 키 · 다른 본문" 이라 422 다.
 */
function DraftRow({ childId, draft }: { childId: string; draft: EventDraft }) {
  const queryClient = useQueryClient();
  const removeDraft = useEventDraftStore((s) => s.removeDraft);
  const submitKey = useIdempotencyKey();
  const [submitted, setSubmitted] = useState(false);

  const submit = useMutation({
    mutationFn: (body: { event: EventDraftFields; items: EventDraftItem[] }) => {
      /**
       * 🚨 **`op` 에 따라 엔드포인트가 갈린다** (9/21 회의): create 는 `POST`, update 는 `PATCH`.
       *    update 는 키를 받지 않는다 — `items` 가 최종 목록이라 같은 본문을 두 번 보내도
       *    결과가 같다 (`lib/api/idempotency.ts` 의 주석).
       */
      if (draft.op === "update" && draft.event_id) {
        return updateEventDraft(draft.event_id, body);
      }
      return submitEventDraft(
        childId,
        { ...body, ...(draft.suggestion_id ? { suggestion_id: draft.suggestion_id } : {}) },
        submitKey.current(),
      );
    },
    onSuccess: async () => {
      // 🚨 성공한 뒤에만 다음 키로 넘어간다. 실패 뒤 다시 누르는 것은 재시도라 같은 키여야 한다.
      submitKey.rotate();
      setSubmitted(true);
      // 🚨 스토어에서 뺀다 — 다음에 열었을 때 이미 넣은 일정이 초안으로 되살아나지 않게.
      removeDraft(childId, draft.draft_id);
      // 일정 하나가 홈 카운트·캘린더를 동시에 바꾼다. 아이 스코프를 통째로 무효화한다.
      await queryClient.invalidateQueries({ queryKey: qk.child(childId) });
    },
  });

  const state: DraftSubmitState = submitted
    ? "submitted"
    : submit.isPending
      ? "submitting"
      : submit.isError
        ? "failed"
        : "idle";

  return (
    <EventDraftCard
      draft={draft}
      state={state}
      error={
        submit.isError
          ? submit.error instanceof Error
            ? submit.error.message
            : "넣지 못했어요."
          : undefined
      }
      onSubmit={(final) => submit.mutate(final)}
    />
  );
}
