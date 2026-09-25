"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { EventDraftCard, type DraftSubmitState } from "@/components/event-draft-card";
import { Button } from "@/components/ui/button";
import { ICON_SIZE, ICON_STROKE } from "@/components/ui/icon";
import {
  qk,
  submitEventDraft,
  updateEventDraft,
  type EventDraft,
  type EventDraftFields,
  type EventDraftItem,
} from "@/lib/api";
import { useIdempotencyKey } from "@/lib/api/use-idempotency-key";
import {
  mergeDrafts,
  useEventDraftStore,
  NO_DRAFTS,
  type DraftOrigin,
  type StoredDraft,
} from "@/stores/event-draft";

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
 *
 * ## 🚨 여러 장이면 **한 장씩 넘긴다.** 세로로 쭉 늘어놓지 않는다
 *
 * 알림장 한 장에서 초안이 셋씩 나오는데, 카드는 제목·일시·하루 종일·준비물·제출까지 든
 * **긴 물건**이다. 세로로 쌓으면 화면 서너 폭이 되고 부모는 스크롤로만 전체를 안다 —
 * 05 제안 목록에서 "카드 더미로 펼쳐 놓지 않는다" 고 정한 것과 같은 이유다
 * (`suggestion-list.tsx` 머리말). 다만 거기는 **훑고 하나만 여는** 것이고 여기는 **하나씩
 * 처리하고 넘기는** 것이라, 접는 대신 한 장씩 보여준다.
 *
 * 🚨 **덤으로 `btn-approve` 가 화면에 하나만 선다.** 건별 제출(9/21)이라 카드마다 승인 버튼이
 *    붙는데, 세로로 쌓으면 한 화면에 셋이 서서 디자인 시스템 §7 "한 화면에 primary 하나" 와
 *    부딪혔다. 한 장씩 넘기면 그 문제가 구조적으로 사라진다 — 05 의 열린 줄이 하나뿐이라
 *    초록 버튼도 하나인 것과 같은 해법이다.
 *
 * 🚨 **넘겼다고 자동으로 다음 장이 열리지 않는다.** 제출한 뒤 화면이 저절로 움직이면 방금 무엇을
 *    넣었는지 확인할 자리가 사라진다 — 넣은 카드는 "넣었어요" 로 그 자리에 남고, 넘기는 것은
 *    부모가 한다. 대신 **몇 건 중 몇 번째인지와 몇 건을 넣었는지**를 항상 말한다.
 */
export function EventDraftList({
  childId,
  /** 이번 run 이 내보낸 묶음. 🚨 스토어에 얹는 것은 **처음 보는 것만**이다 (아래 `seen`). */
  incoming,
  origin,
  found,
}: {
  childId: string;
  incoming: EventDraft[];
  /**
   * 어느 경로의 목록인가. 🚨 **스토어에서 되읽을 때 이 값으로 거른다** — 한 아이의 초안을
   *    통째로 그리면 04 에서 안 낸 초안이 08 화면에 "사진에서 읽어냈어요" 를 달고 선다.
   */
  origin: DraftOrigin;
  /**
   * 어디서 찾은 일정인지 한 줄. 🚨 **그릇이 넘긴다 — 목록이 지어내지 않는다.**
   *    여기 "적어주신 말에서 찾았어요" 를 박아 뒀더니 08 사진 화면에도 그대로 떴다.
   *    카드의 `hint` 에서 낸 것과 같은 사고라, 출처를 아는 쪽이 말하게 한다.
   */
  found: string;
}) {
  const addDrafts = useEventDraftStore((s) => s.addDrafts);
  const stored = useEventDraftStore((s) => s.byChild[childId] ?? NO_DRAFTS);

  /**
   * 🚨 **그리는 목록의 정본은 여기다.** 스토어에서 바로 그리면 제출한 카드가 그 순간 사라져서
   *    "넣었어요" 를 볼 자리가 없다. 스토어는 **다음에 다시 열었을 때 뭐가 남아 있는가**를 지고,
   *    이 배열은 **지금 화면에 뭐가 서 있는가**를 진다.
   *
   * 🚨 **같은 경로의 것만 되읽는다** (위 `origin`). 처음엔 아이 것을 통째로 seed 했는데,
   *    그러면 04 의 초안이 08 화면에 다른 문구를 달고 섰다.
   */
  const [rows, setRows] = useState<StoredDraft[]>(() => stored.filter((d) => d.origin === origin));

  /**
   * 🚨 이미 얹은 초안을 다시 얹지 않는다. 제출하고 스토어에서 뺀 장이 effect 가 다시 돌 때
   *    되살아나는 것을 막는다 — 그러면 이미 캘린더에 넣은 일정이 초안으로 부활한다.
   */
  const seen = useRef(new Set<string>());

  useEffect(() => {
    const fresh = incoming.filter((d) => !seen.current.has(d.draft_id));
    if (fresh.length === 0) return;
    for (const d of fresh) seen.current.add(d.draft_id);
    addDrafts(childId, origin, fresh);
    // 🚨 **스토어와 같은 규칙으로 합친다.** `draft_id` 만 거르면 같은 `event_id` 의 낡은 장이
    //    화면에 남아 자기 제출 버튼을 갖고, 그걸 나중에 누르면 새 장의 준비물이 지워진다.
    setRows((prev) =>
      mergeDrafts(
        prev,
        fresh.map((draft) => ({ origin, draft })),
      ),
    );
  }, [incoming, childId, origin, addDrafts]);

  /** 지금 보고 있는 장. 🚨 목록이 줄어들면 범위를 넘을 수 있어 그릴 때 한 번 더 조인다. */
  const [page, setPage] = useState(0);
  /** 넣은 장. 🚨 카드가 아니라 목록이 든다 — "3건 중 1건 넣었어요" 를 말하려면 여기서 세야 한다. */
  const [submittedIds, setSubmittedIds] = useState<string[]>([]);

  if (rows.length === 0) return null;

  const index = Math.min(page, rows.length - 1);
  const current = rows[index];
  const done = submittedIds.length;
  const multiple = rows.length > 1;

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

      {/* 🚨 `key` 에 `draft_id` 를 준다. 안 주면 넘길 때 React 가 같은 카드를 재사용해서
          앞 장에서 고친 값(제목 · 날짜 · 준비물)이 다음 장에 그대로 남는다. */}
      <DraftRow
        key={current.draft.draft_id}
        childId={childId}
        draft={current.draft}
        onSubmitted={(draftId) =>
          setSubmittedIds((prev) => (prev.includes(draftId) ? prev : [...prev, draftId]))
        }
      />

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

            {/* 🚨 넘긴 것을 **소리로도** 알린다. 카드가 통째로 바뀌는데 아무 말이 없으면
                화면을 안 보는 사람에게는 아무 일도 안 일어난 것과 같다. */}
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

          {/* 🚨 **넣은 건수를 말한다.** 한 장씩 보여주면 남은 것이 화면 밖이라, 한 장 넣고
              다 끝난 줄 알고 나가는 길이 생긴다. */}
          {done > 0 ? (
            <p role="status" className="text-body-sm text-ink-muted text-center">
              {done === rows.length
                ? `${rows.length}건 전부 넣었어요.`
                : `${rows.length}건 중 ${done}건 넣었어요.`}
            </p>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}

/**
 * 초안 한 장의 제출. 🚨 **카드마다 이 컴포넌트가 하나씩**이라 `useIdempotencyKey()` 도 하나씩이다 —
 * 하나로 묶으면 두 번째 제출이 "같은 키 · 다른 본문" 이라 422 다.
 */
function DraftRow({
  childId,
  draft,
  onSubmitted,
}: {
  childId: string;
  draft: EventDraft;
  onSubmitted: (draftId: string) => void;
}) {
  const queryClient = useQueryClient();
  const removeDraft = useEventDraftStore((s) => s.removeDraft);
  const submitKey = useIdempotencyKey();
  const [submitted, setSubmitted] = useState(false);

  const submit = useMutation({
    mutationFn: (body: { event: EventDraftFields; items: EventDraftItem[] }) => {
      /**
       * 🚨 **`op` 에 따라 엔드포인트가 갈린다** (9/21 회의): create 는 `POST`, update 는 `PATCH`.
       *    🚨 **둘 다 같은 키를 쓴다** — PATCH 도 `item_id: null` 인 새 준비물 때문에 멱등이 아니다
       *    (`operations.ts` 의 주석).
       */
      if (draft.op === "update" && draft.event_id) {
        return updateEventDraft(draft.event_id, body, submitKey.current());
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
      onSubmitted(draft.draft_id);
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
