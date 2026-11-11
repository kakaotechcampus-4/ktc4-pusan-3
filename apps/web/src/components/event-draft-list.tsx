"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import {
  EventDraftCard,
  SeriesDraftCard,
  type DraftSubmitFinal,
  type DraftSubmitState,
} from "@/components/event-draft-card";
import { Button } from "@/components/ui/button";
import { ICON_SIZE, ICON_STROKE } from "@/components/ui/icon";
import {
  isApiError,
  qk,
  submitEventDraft,
  updateEventDraft,
  updateEventSeries,
  type AnyEventDraft,
  type EventDraft,
  type IdempotencyKey,
  type SeriesDraft,
} from "@/lib/api";
import { useIdempotencyKey } from "@/lib/api/use-idempotency-key";
import { cn } from "@/lib/cn";
import { isSeriesDraft, seriesSubmitBody } from "@/lib/event-draft";
import {
  mergeDrafts,
  useEventDraftStore,
  NO_DRAFTS,
  type DraftOrigin,
  type StoredDraft,
} from "@/stores/event-draft";

/**
 * 일정 초안 묶음 — **04 대화(찾은 일정 시트) · 05 승인 시트 · 08 사진이 같이 쓴다.**
 *
 * 🚨 **제출은 건별이다** (9/21 회의). 카드마다 자기 버튼·자기 키·자기 결과를 갖는다.
 *    한 요청으로 묶지 않으므로 3장 중 1장이 실패해도 나머지 2장은 이미 들어가 있다 (NF-06).
 *
 * 🚨 **초안은 서버에 없다.** 들어온 묶음을 세션 스토리지에 넣고(`stores/event-draft.ts`),
 *    그 스토어가 같은 `event_id` 를 최신 한 장으로 줄인다 — 낡은 초안이 뒤 결과를 지우는 것을
 *    막는 1차 방어다 (#122). 🚨 05 승인 시트는 예외다 — 넣지도 되읽지도 않는다 (`transient` · #539).
 *
 * 🚨 **넣은 초안은 스토어에서 뺀다.** 안 빼면 다음에 이 화면을 열 때 이미 캘린더에 있는 일정이
 *    "아직 저장되지 않았어요" 로 다시 뜬다. 다만 **화면에서는 그 자리에 남겨** 무엇이 들어갔는지
 *    보이게 한다 — 그래서 그리는 목록(`rows`)과 남겨 두는 스토어가 다른 값이다.
 *
 * ## 🚨 여러 장이면 **한 장씩 넘긴다.** 세로로 쭉 늘어놓지 않는다
 *
 * 알림장 한 장에서 초안이 셋씩 나오는데, 카드는 제목·일시·하루 종일·준비물·제출까지 든
 * **긴 물건**이다. 세로로 쌓으면 화면 서너 폭이 되고 부모는 스크롤로만 전체를 안다.
 *
 * 🚨 **05 제안 목록과는 반대로 간다.** 거기도 후보가 여럿인데 접지 않고 전부 펼친다
 *    (`suggestion-list.tsx` 머리말) — **고르는 화면**이라 셋을 견주려면 이유와 근거가 같이
 *    보여야 하기 때문이다. 여기는 고르는 화면이 아니라 **하나씩 처리하고 넘기는** 화면이고,
 *    줄이 아니라 **폼**이라 견줄 것도 없다. 같은 "여럿" 을 한쪽은 펼치고 한쪽은 넘기는 이유다.
 *
 * 🚨 **덤으로 `btn-approve` 가 화면에 하나만 선다.** 건별 제출(9/21)이라 카드마다 승인 버튼이
 *    붙는데, 세로로 쌓으면 한 화면에 셋이 서서 디자인 시스템 §7 "한 화면에 primary 하나" 와
 *    부딪혔다. 한 장씩 넘기면 그 문제가 구조적으로 사라진다. 05 는 같은 문제를 **다르게**
 *    풀었다 — 거기는 고르는 것을 버튼이 아니라 체크박스(제안 문장이 라벨)로 만들어
 *    초록을 하단 CTA 하나로 몰았다.
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
  blockedDraftIds,
  lockReason,
  draftIds,
  transient = false,
  heading = true,
  onAllergenBlocked,
  onSuggestionsSubmitted,
}: {
  childId: string;
  /** 🚨 반복 묶음 고치기(#738)가 섞여 온다 — 장마다 `op` 로 카드를 가른다 (`DraftRow`). */
  incoming: AnyEventDraft[];
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
  /**
   * 🚨 **넣지 않을 초안** (알레르기로 막힌 것). 그릇이 판정한다 — 사전검사에 답하는 자리는
   *    목록 밖(승인 시트)이고, 여기는 결과만 받는다.
   * 🚨 **막히는 것은 그 초안뿐이다.** 식사 초안의 알레르기 때문에 놀이 초안까지 막으면 버그다.
   */
  blockedDraftIds?: string[];
  /** 아직 못 넣는 이유 (확인이 안 끝남). 🚨 `blockedDraftIds`(안 넣는다)와 다른 값이다. */
  lockReason?: string;
  /**
   * 스토어에서 되읽을 장을 **이 run 의 것으로** 좁힌다 (04 대화 · #226). 대화에는 run 이 여럿이라
   * `origin` 만으로 거르면 한 답의 시트에 다른 답의 초안까지 선다. 🚨 이때는 `incoming` 을 비워
   * 넘긴다 — 얹는 것은 대화 스토어가 도착할 때 한 번 했다 (`stores/conversation.ts` 의 `seedDrafts`).
   */
  draftIds?: string[];
  /**
   * 이 묶음을 **보관함에 두지 않는다** (05 승인 시트 · #539). 되읽지도 얹지도 않고, 열 때 같은 경로의
   * 남은 초안을 지운다. 05 는 시트를 열 때마다 서버에서 초안을 새로 받으므로 둘 이유가 없다 —
   * 이유는 `stores/event-draft.ts` 머리말. 🚨 되읽지 않으므로 `draftIds` 와 같이 쓰지 않는다.
   */
  transient?: boolean;
  /**
   * 머리글 질문("일정으로 만들까요?")을 세우는가. 🚨 **그릇의 제목이 이미 그 질문이면 끈다** (#868 · 06 승인 시트의
   * "캘린더에 넣을까요"). 둘 다 서면 시트 머리에 같은 질문이 두 번 섰다. `found` 와 "넣기 전까지는" 줄은 남는다.
   */
  heading?: boolean;
  /**
   * 넣는 순간 서버가 알레르기로 막은 제안 id (#902). 그 카드는 여기서 잠그고, id 는 그릇에 올린다 — 05 는 시트를
   *    닫은 뒤 다시 만들 때 그 제안을 뺀다(#859 와 같은 상태). 🚨 막는 것은 서버다. 화면은 결과만 옮긴다.
   */
  onAllergenBlocked?: (suggestionIds: string[]) => void;
  /**
   * 캘린더에 넣은 초안의 제안 id (#902 리뷰). 05 는 시트를 닫은 뒤 다시 만들 때 이것도 뺀다 — 안 빼면 이미 넣은
   *    제안이 초안으로 다시 서고, 넣으면 `409 already_confirmed` 다. 🚨 서버가 넣었다고 답한 뒤에만 부른다.
   */
  onSuggestionsSubmitted?: (suggestionIds: string[]) => void;
}) {
  const addDrafts = useEventDraftStore((s) => s.addDrafts);
  const dropDrafts = useEventDraftStore((s) => s.dropDrafts);
  const stored = useEventDraftStore((s) => s.byChild[childId] ?? NO_DRAFTS);

  /**
   * 🚨 **그리는 목록의 정본은 여기다.** 스토어에서 바로 그리면 제출한 카드가 그 순간 사라져서
   *    "넣었어요" 를 볼 자리가 없다. 스토어는 **다음에 다시 열었을 때 뭐가 남아 있는가**를 지고,
   *    이 배열은 **지금 화면에 뭐가 서 있는가**를 진다.
   *
   * 🚨 **같은 경로의 것만 되읽는다** (위 `origin`). 처음엔 아이 것을 통째로 seed 했는데,
   *    그러면 04 의 초안이 08 화면에 다른 문구를 달고 섰다.
   */
  const [rows, setRows] = useState<StoredDraft[]>(() =>
    transient
      ? []
      : stored.filter(
          (d) =>
            d.origin === origin && (draftIds === undefined || draftIds.includes(d.draft.draft_id)),
        ),
  );

  /**
   * 🚨 이미 얹은 초안을 다시 얹지 않는다. 제출하고 스토어에서 뺀 장이 effect 가 다시 돌 때
   *    되살아나는 것을 막는다 — 그러면 이미 캘린더에 넣은 일정이 초안으로 부활한다.
   */
  const seen = useRef(new Set<string>());

  useEffect(() => {
    if (transient) dropDrafts(childId, origin);
  }, [transient, childId, origin, dropDrafts]);

  useEffect(() => {
    const fresh = incoming.filter((d) => !seen.current.has(d.draft_id));
    if (fresh.length === 0) return;
    for (const d of fresh) seen.current.add(d.draft_id);
    if (!transient) addDrafts(childId, origin, fresh);
    // 🚨 **스토어와 같은 규칙으로 합친다.** `draft_id` 만 거르면 같은 `event_id` 의 낡은 장이
    //    화면에 남아 자기 제출 버튼을 갖고, 그걸 나중에 누르면 새 장의 준비물이 지워진다.
    setRows((prev) =>
      mergeDrafts(
        prev,
        fresh.map((draft) => ({ origin, draft })),
      ),
    );
  }, [incoming, childId, origin, transient, addDrafts]);

  /** 지금 보고 있는 장. 🚨 목록이 줄어들면 범위를 넘을 수 있어 그릴 때 한 번 더 조인다. */
  const [page, setPage] = useState(0);
  /** 넣은 장. 🚨 카드가 아니라 목록이 든다 — "3건 중 1건 넣었어요" 를 말하려면 여기서 세야 한다. */
  const [submittedIds, setSubmittedIds] = useState<string[]>([]);

  if (rows.length === 0) return null;

  const index = Math.min(page, rows.length - 1);
  const done = submittedIds.length;
  const multiple = rows.length > 1;
  /** 🚨 반복 묶음 고치기(#738)가 섞이면 "넣었어요" 가 거짓말이 된다 — 고친 것은 넣은 것이 아니다. */
  const finished = rows.some((row) => isSeriesDraft(row.draft)) ? "마쳤어요" : "넣었어요";

  return (
    <section className="flex flex-col gap-3">
      {/* 🚨 **"저장했어요" 와 같은 머리글을 쓰지 않는다.** 위의 관찰·기억은 이미 저장된 것이고
          여기는 보호자가 넣어야 들어가는 것이다 — 머리글이 그 선을 긋는다. */}
      <div>
        {heading ? <h3 className="text-label text-brand">일정으로 만들까요?</h3> : null}
        <p className={cn("text-body-sm text-ink-muted", heading && "mt-1")}>
          {found} 넣기 전까지는 캘린더에 들어가지 않아요.
        </p>
      </div>

      {/* 🚨 **모든 장을 마운트해 두고 지금 장만 보인다** (#537 리뷰). 지금 장만 그리면 넘길 때 앞 장이 언마운트되면서
          제출 상태와 키 홀더가 같이 사라졌다 — 넣은 장으로 돌아오면 "아직 저장되지 않았어요" 로 되살아나 **새 키로
          한 번 더** 넣을 수 있었다(일정 두 건). 결과를 확인하지 못한 제출 뒤 넘겼다 돌아와 다시 누르는 것도 새 키였다.
          🚨 `key` 는 `draft_id` 다 — 장마다 자기 칸 값(제목 · 날짜 · 준비물)과 자기 키를 든다. */}
      {rows.map((row, i) => (
        <div key={row.draft.draft_id} hidden={i !== index}>
          <DraftRow
            childId={childId}
            draft={row.draft}
            blocked={blockedDraftIds?.includes(row.draft.draft_id) ?? false}
            lockReason={lockReason}
            onAllergenBlocked={onAllergenBlocked}
            onSubmitted={(draftId) => {
              setSubmittedIds((prev) => (prev.includes(draftId) ? prev : [...prev, draftId]));
              const ids = isSeriesDraft(row.draft) ? [] : (row.draft.suggestion_ids ?? []);
              if (ids.length > 0) onSuggestionsSubmitted?.(ids);
            }}
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
                ? `${rows.length}건 전부 ${finished}.`
                : `${rows.length}건 중 ${done}건 ${finished}.`}
            </p>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}

/**
 * 초안 한 장. 🚨 **`op` 로 카드를 가른다** — 반복 묶음 고치기(#738)는 일정 카드의 모양이 아니다.
 * 두 갈래 모두 승인 게이트 ㉠ 이고, 제출 · 키 · 성공 뒤 처리는 `useDraftSubmit` 하나가 진다.
 */
function DraftRow(props: {
  childId: string;
  draft: AnyEventDraft;
  blocked: boolean;
  lockReason?: string;
  onAllergenBlocked?: (suggestionIds: string[]) => void;
  onSubmitted: (draftId: string) => void;
}) {
  const { draft } = props;
  return isSeriesDraft(draft) ? (
    <SeriesDraftRow {...props} draft={draft} />
  ) : (
    <EventDraftRow {...props} draft={draft} />
  );
}

/**
 * 초안 한 장의 제출. 🚨 **카드마다 이 훅이 하나씩**이라 `useIdempotencyKey()` 도 하나씩이다 —
 * 하나로 묶으면 두 번째 제출이 "같은 키 · 다른 본문" 이라 422 다.
 */
const REPEAT_FIX_HINT = "반복하는 요일이나 날짜를 바꾸려면 대화로 다시 말해 주세요.";
/**
 * 동의가 없어 막힌 제출 (#902). 🚨 "같은 버튼으로" 라고 약속하지 않는다 — 05 초안은 저장하지 않아서(#539) 이 탭에서
 * 설정으로 가면 시트와 카드가 사라진다. 돌아와 다시 만드는 것이 보호자가 실제로 하게 되는 일이다. 버튼은 남긴다 —
 * 다른 탭 · 기기에서 켰으면 서버가 403 을 기억하지 않아 같은 키로 들어간다.
 */
const CONSENT_FIX_HINT = "설정 화면의 동의에서 켠 뒤 일정을 다시 만들어 주세요.";

function useDraftSubmit<Body>(
  childId: string,
  draftId: string,
  onSubmitted: (draftId: string) => void,
  send: (body: Body, key: IdempotencyKey) => Promise<unknown>,
  {
    fallbackError = "넣지 못했어요.",
    onError,
  }: {
    /** 서버가 이유를 주지 않았을 때의 말. 🚨 묶음 고치기는 "넣지" 가 아니라 "바꾸지" 못한 것이다. */
    fallbackError?: string;
    /** 실패 응답을 그릇에 올릴 때 (#902 · 알레르기로 막힌 제안 id). */
    onError?: (error: Error) => void;
  } = {},
) {
  const queryClient = useQueryClient();
  const removeDraft = useEventDraftStore((s) => s.removeDraft);
  const submitKey = useIdempotencyKey();
  const [submitted, setSubmitted] = useState(false);

  const submit = useMutation({
    mutationFn: (body: Body) => send(body, submitKey.current()),
    onSuccess: async () => {
      // 🚨 성공한 뒤에만 다음 키로 넘어간다. 실패 뒤 다시 누르는 것은 재시도라 같은 키여야 한다.
      submitKey.rotate();
      setSubmitted(true);
      onSubmitted(draftId);
      // 🚨 스토어에서 뺀다 — 다음에 열었을 때 이미 넣은 일정이 초안으로 되살아나지 않게.
      removeDraft(childId, draftId);
      // 일정 하나가 홈 카운트·캘린더를 동시에 바꾼다. 아이 스코프를 통째로 무효화한다.
      await queryClient.invalidateQueries({ queryKey: qk.child(childId) });
    },
    onError,
  });

  const state: DraftSubmitState = submitted
    ? "submitted"
    : submit.isPending
      ? "submitting"
      : submit.isError
        ? "failed"
        : "idle";
  const error = submit.isError
    ? submit.error instanceof Error
      ? submit.error.message
      : fallbackError
    : undefined;

  /**
   * 🚨 반복 규칙 오류는 카드에서 고칠 수 없다 (`invalid_repeat` · #761). 요일 · 끝 날짜 · 첫 회차를 고칠 칸이 없어서
   *    같은 초안으로 다시 눌러도 같은 422 다 — 어떻게 하면 되는지를 서버 문구 아래에 붙인다.
   */
  const errorHint = isApiError(submit.error, "invalid_repeat")
    ? REPEAT_FIX_HINT
    : isApiError(submit.error, "consent_required")
      ? CONSENT_FIX_HINT
      : undefined;

  return { state, error, errorHint, mutate: submit.mutate, failure: submit.error };
}

function EventDraftRow({
  childId,
  draft,
  blocked,
  lockReason,
  onAllergenBlocked,
  onSubmitted,
}: {
  childId: string;
  draft: EventDraft;
  blocked: boolean;
  lockReason?: string;
  onAllergenBlocked?: (suggestionIds: string[]) => void;
  onSubmitted: (draftId: string) => void;
}) {
  const { state, error, errorHint, mutate, failure } = useDraftSubmit<DraftSubmitFinal>(
    childId,
    draft.draft_id,
    onSubmitted,
    (body, key) => {
      /**
       * 🚨 **`op` 에 따라 엔드포인트가 갈린다** (9/21 회의): create 는 `POST`, update 는 `PATCH`.
       *    🚨 **둘 다 같은 키를 쓴다** — PATCH 도 `item_id: null` 인 새 준비물 때문에 멱등이 아니다
       *    (`operations.ts` 의 주석).
       */
      if (draft.op === "update" && draft.event_id) {
        // 🚨 수정은 `reminder` 를 싣지 않는다 (`event-draft-card.tsx` 머리말).
        const update = { event: body.event, items: body.items };
        return updateEventDraft(childId, draft.event_id, update, key);
      }
      // 🚨 묶인 초안은 여러 제안에서 온다 — 전부 이 일정에 연결돼야 한다 (#206).
      // 🚨 반복 일정(#738)은 카드가 넘긴 `repeat` 가 그대로 실린다 — 빠지면 하루짜리 일정 한 건이 된다.
      const create = {
        ...body,
        ...(draft.suggestion_ids ? { suggestion_ids: draft.suggestion_ids } : {}),
      };
      return submitEventDraft(childId, create, key);
    },
    {
      onError: (e) => {
        const ids = allergenBlockedIds(e, draft.suggestion_ids ?? []);
        if (ids) onAllergenBlocked?.(ids);
      },
    },
  );

  /**
   * 🚨 **넣는 순간 서버가 알레르기로 막았다** (`409 allergen_blocked` · 서버 #902 · 문서 §3-8). 초안을 만든 뒤
   *    다른 보호자가 등록한 알레르기다. 다시 눌러도 같은 409 라서 실패 카드(서버 문구 + 다시 누를 수 있는 버튼)로
   *    그리지 않고, 그 카드를 잠근 채 안내한다. 🚨 **막히는 것은 이 카드뿐이다** — 제출은 카드 한 장에 요청
   *    하나라서 다른 카드는 그대로 넣을 수 있다.
   */
  const lateBlocked = allergenBlockedIds(failure, draft.suggestion_ids ?? []);
  /** 묶인 식사 초안이면 걸리지 않은 제안이 남는다 — 05 에서 다시 만들면 그것만으로 만들어진다. */
  const othersLeft = (draft.suggestion_ids ?? []).some((id) => !lateBlocked?.includes(id));

  return (
    <EventDraftCard
      draft={draft}
      state={state}
      blocked={blocked || lateBlocked !== null}
      blockedNote={
        lateBlocked === null
          ? undefined
          : othersLeft
            ? "그사이 알레르기로 등록된 식품이 들어간 추천이 있어서 이 일정은 넣지 않았어요. 닫고 일정으로 다시 만들면 그 추천을 빼고 만들 수 있어요."
            : "그사이 알레르기로 등록된 식품이 들어간 추천이라 이 일정은 넣지 않았어요."
      }
      lockReason={lockReason}
      error={lateBlocked === null ? error : undefined}
      errorHint={errorHint}
      onSubmit={(final) => mutate(final)}
    />
  );
}

/**
 * `409 allergen_blocked` 면 걸린 제안 id, 아니면 `null`. ⚠️ 서버가 어느 제안인지 안 실으면 이 초안의 제안
 * 전부로 본다 — 알레르기에서 덜 막는 쪽으로 기울 수 없다 (05 의 채택 · 초안 만들기 409 와 같다).
 */
function allergenBlockedIds(error: unknown, sent: string[]): string[] | null {
  if (!isApiError(error, "allergen_blocked")) return null;
  const listed = error.detail?.suggestion_ids;
  return Array.isArray(listed) && listed.length > 0 && listed.every((id) => typeof id === "string")
    ? (listed as string[])
    : sent;
}

/**
 * 반복 묶음 고치기 한 장 (#738). 🚨 카드가 읽기 전용이라 **초안 값을 그대로** 보낸다 (`seriesSubmitBody`).
 * 🚨 묶음 초안은 한 줄 입력에서만 온다 — 제안 경로의 알레르기 잠금(`blocked` · `lockReason`)이 걸릴 일이 없다.
 */
function SeriesDraftRow({
  childId,
  draft,
  onSubmitted,
}: {
  childId: string;
  draft: SeriesDraft;
  onSubmitted: (draftId: string) => void;
}) {
  const { state, error, errorHint, mutate } = useDraftSubmit<SeriesDraft>(
    childId,
    draft.draft_id,
    onSubmitted,
    (body, key) => updateEventSeries(childId, body.series_id, seriesSubmitBody(body), key),
    { fallbackError: "바꾸지 못했어요." },
  );

  return (
    <SeriesDraftCard
      draft={draft}
      state={state}
      error={error}
      errorHint={errorHint}
      onSubmit={() => mutate(draft)}
    />
  );
}
