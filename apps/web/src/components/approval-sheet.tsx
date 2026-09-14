"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";

import { Banner } from "@/components/ui/banner";
import { BottomSheet } from "@/components/ui/bottom-sheet";
import { Button } from "@/components/ui/button";
import { CardFailed } from "@/components/ui/card";
import { Chip, ChipRow } from "@/components/ui/chip";
import { Spinner } from "@/components/ui/spinner";
import {
  addHealthSafety,
  confirmEvent as confirmEventRequest,
  newIdempotencyKey,
  qk,
  type CreateEventResponse,
  type ConfirmEventResponse,
  type CreateHealthSafetyRequest,
  type IdempotencyKey,
  type Precheck,
  type Suggestion,
} from "@/lib/api";
import { useIdempotencyKey } from "@/lib/api/use-idempotency-key";
import { formatEventTime } from "@/lib/format";

/**
 * 06 승인 시트 — **되돌릴 수 없는 두 곳이 여기 다 있다** (CLAUDE.md §2 · §3).
 *
 *   ㉡ 건강·알레르기 기록 확정   POST /children/{cid}/health-safety
 *   ㉠ 캘린더 쓰기               POST /events/{eid}/confirm
 *
 * 🚨 **승인 게이트를 늘리지도 줄이지도 않는다.** 초안(`POST /suggestions/{sid}/event`)은
 *    게이트가 아니다 — 24시간 뒤 만료되는 되돌릴 수 있는 상태라 물어보지 않는다.
 *
 * 🚨 **낙관적 업데이트를 쓰지 않는다.** `onMutate` 로 캐시를 먼저 바꾸면 서버가 확정하기 전에
 *    화면이 이미 확정된 것처럼 보인다 — "되돌릴 수 없는 것은 사람이 승인한다" 가 시각적으로 깨진다.
 * 🚨 **자동 재시도가 꺼져 있다** (`query-client.ts`). 중복 실행이 캘린더에 두 번 쓴다.
 * 🚨 **재시도할 때 Idempotency-Key 를 새로 만들지 않는다.** 같은 키를 다시 보내는 것이
 *    중복 실행을 막는 유일한 장치다.
 * 🚨 **스크림·ESC 로 닫히지 않는다** (`dismissible={false}`). 실수로 닫혀서 draft 가
 *    만료되는 경로를 만들지 않는다.
 */

/** 알레르기 확인은 3지선다다. 🚨 "모르겠어요" 는 "없음" 이 아니다 — 아무것도 저장하지 않는다. */
type SafetyAnswer = "has" | "none" | "unknown";

const SAFETY_CHOICES: Array<{ value: SafetyAnswer; label: string }> = [
  { value: "none", label: "먹어봤어요 · 괜찮았어요" },
  { value: "has", label: "알레르기가 있어요" },
  { value: "unknown", label: "모르겠어요" },
];

export function ApprovalSheet({
  open,
  onClose,
  childId,
  suggestion,
  draft,
}: {
  open: boolean;
  onClose: () => void;
  childId: string;
  suggestion: Suggestion;
  /** `POST /suggestions/{sid}/event` 의 응답. 초안과 사전검사가 한 덩어리로 온다. */
  draft: CreateEventResponse;
}) {
  const queryClient = useQueryClient();

  const [answers, setAnswers] = useState<Record<string, SafetyAnswer>>({});
  const [confirmed, setConfirmed] = useState<ConfirmEventResponse | null>(null);

  /**
   * 🚨 **재료 한 건이 사용자 동작 하나다.** 그래서 `useIdempotencyKey()`(동작 하나에 키 하나)가
   *    아니라 재료별 맵이다 — 같은 재료를 다시 저장하면 같은 키가 나가고(재시도), 다른 재료는
   *    다른 키가 나간다. 하나로 묶으면 두 번째 재료가 "같은 키 · 다른 요청" 이라 422 다.
   */
  const safetyKeys = useRef(new Map<string, IdempotencyKey>());
  /** 확정은 동작 하나다. 재시도는 같은 키, 성공한 뒤에만 다음 키로 넘어간다. */
  const confirmKey = useIdempotencyKey();

  const ingredientChecks = draft.prechecks.filter((p) => p.code === "unknown_ingredient");
  const answeredAll = ingredientChecks.every((p) => answers[p.item] !== undefined);
  /** 🚨 알레르기가 확인된 재료가 하나라도 있으면 이 제안은 나가지 않는다 (문서 §11). */
  const blocked = ingredientChecks.some((p) => answers[p.item] === "has");

  /** 승인 게이트 ㉡ — 보호자가 확인한 값만 들어간다. LLM 이 추론한 값은 여기 오지 않는다 (NF-03). */
  const saveSafety = useMutation({
    mutationFn: (item: string) => {
      const key = safetyKeys.current.get(item) ?? newIdempotencyKey();
      safetyKeys.current.set(item, key);
      const body: CreateHealthSafetyRequest = {
        type: "allergy",
        label: item,
        category: "식품",
        notes: "승인 화면에서 보호자가 확인",
      };
      return addHealthSafety(childId, body, key);
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: qk.healthSafety(childId) }),
  });

  /** 승인 게이트 ㉠ — 되돌릴 수 없는 지점. */
  const confirmEvent = useMutation({
    mutationFn: () => confirmEventRequest(draft.event.id, confirmKey.current()),
    onSuccess: async (result) => {
      // 🚨 다음 동작으로 넘어가는 것은 **성공 응답을 받은 뒤**다. 실패 뒤 다시 누르는 것은
      //    같은 키로 가야 재시도로 취급된다.
      confirmKey.rotate();
      setConfirmed(result);
      // 일정 하나가 홈 카운트·캘린더·제안 상태를 동시에 바꾼다. 아이 스코프를 통째로 무효화한다.
      await queryClient.invalidateQueries({ queryKey: qk.child(childId) });
    },
  });

  function answer(item: string, value: SafetyAnswer) {
    setAnswers((prev) => ({ ...prev, [item]: value }));
    // 🚨 "있어요" 일 때만 저장한다. "괜찮았어요" 는 알레르기 기록이 아니고,
    //    "모르겠어요" 는 아무것도 저장하지 않는다.
    if (value === "has") saveSafety.mutate(item);
  }

  const busy = confirmEvent.isPending || saveSafety.isPending;

  return (
    <BottomSheet
      open={open}
      onClose={onClose}
      dismissible={false}
      title={confirmed ? "캘린더에 넣었어요" : "확인해 주세요"}
      description={
        confirmed
          ? "함께 보는 보호자에게도 공유됐어요."
          : "승인하기 전에는 아무것도 저장되지 않아요."
      }
      footer={
        confirmed ? (
          <Button block onClick={onClose}>
            닫기
          </Button>
        ) : (
          <div className="flex flex-col gap-2">
            {blocked ? (
              <p className="text-body-sm text-danger-ink">
                알레르기를 확인했어요. 이 제안은 넣지 않을게요.
              </p>
            ) : (
              <Button
                variant="approve"
                onClick={() => confirmEvent.mutate()}
                disabled={busy || !answeredAll}
              >
                {confirmEvent.isPending ? <Spinner /> : null}
                {confirmEvent.isPending ? "넣는 중…" : "캘린더에 넣기"}
              </Button>
            )}
            <Button variant="secondary" block onClick={onClose} disabled={busy}>
              {blocked ? "닫기" : "안 넣기"}
            </Button>
            {!answeredAll && !blocked ? (
              <p className="text-caption text-ink-subtle text-center">
                위의 확인이 끝나야 넣을 수 있어요.
              </p>
            ) : null}
          </div>
        )
      }
    >
      {confirmed ? (
        <ConfirmedBody title={confirmed.event.title} />
      ) : (
        <div className="flex flex-col gap-4">
          {blocked ? (
            <Banner tone="danger" title="알레르기 기록에 추가했어요">
              이 재료가 들어간 제안은 넣지 않아요. 앞으로의 식사 제안에서도 걸러내요.
            </Banner>
          ) : ingredientChecks.length > 0 ? (
            <Banner tone="caution" title="처음 보는 재료가 있어요">
              아이 알레르기 기록에 없는 재료예요. 보호자가 확인해 주셔야 넣을 수 있어요.
            </Banner>
          ) : null}

          {ingredientChecks.map((precheck) => (
            <SafetyCheck
              key={precheck.item}
              precheck={precheck}
              value={answers[precheck.item]}
              onAnswer={(value) => answer(precheck.item, value)}
              disabled={saveSafety.isPending}
            />
          ))}

          {saveSafety.isError ? (
            <CardFailed>
              <p>알레르기 기록을 저장하지 못했어요. 저장되기 전까지는 넣지 않을게요.</p>
              <Button
                variant="tertiary"
                size="compact"
                className="mt-3"
                onClick={() => saveSafety.reset()}
              >
                다시 고르기
              </Button>
            </CardFailed>
          ) : null}

          <DraftDetail draft={draft} suggestion={suggestion} />

          {confirmEvent.isError ? (
            <CardFailed>
              <p>
                {confirmEvent.error instanceof Error
                  ? confirmEvent.error.message
                  : "넣지 못했어요."}
              </p>
              <Button
                variant="tertiary"
                size="compact"
                className="mt-3"
                onClick={() => confirmEvent.mutate()}
              >
                다시 시도
              </Button>
            </CardFailed>
          ) : null}
        </div>
      )}
    </BottomSheet>
  );
}

function SafetyCheck({
  precheck,
  value,
  onAnswer,
  disabled,
}: {
  precheck: Precheck;
  value: SafetyAnswer | undefined;
  onAnswer: (value: SafetyAnswer) => void;
  disabled: boolean;
}) {
  return (
    <section className="border-line rounded-card bg-surface border p-4">
      <p className="text-section text-ink">
        {precheck.item} ({precheck.note})
      </p>
      <p className="text-body-sm text-ink-muted mt-1">이 재료를 먹어본 적이 있나요?</p>
      <ChipRow>
        {SAFETY_CHOICES.map((choice) => (
          <Chip
            key={choice.value}
            selected={value === choice.value}
            disabled={disabled}
            onClick={() => onAnswer(choice.value)}
          >
            {choice.label}
          </Chip>
        ))}
      </ChipRow>
      <p className="text-caption text-ink-subtle mt-2">
        보호자가 확인한 값만 건강 기록에 저장돼요. 모르겠어요를 고르면 아무것도 저장하지 않아요.
      </p>
    </section>
  );
}

/** "실제로 저장될 내용 전문" — 무엇이 들어가는지를 줄여 쓰지 않는다. */
function DraftDetail({
  draft,
  suggestion,
}: {
  draft: CreateEventResponse;
  suggestion: Suggestion;
}) {
  const { event } = draft;

  return (
    <section className="border-line rounded-card bg-surface border p-4">
      <h3 className="text-section text-ink">실제로 저장될 내용</h3>
      <dl className="mt-3 flex flex-col gap-2">
        <Row term="제목" value={event.title} />
        <Row term="일시" value={formatEventTime(event.starts_at, event.all_day)} />
        {event.items.length > 0 ? (
          <Row term="준비물" value={event.items.map((item) => item.item_name).join(", ")} />
        ) : null}
        <Row term="근거" value={suggestion.evidence.map((e) => e.label).join(", ")} />
      </dl>
      <p className="text-caption text-ink-subtle mt-3">
        승인하지 않으면 초안으로만 남고 하루 뒤 사라져요.
      </p>
    </section>
  );
}

function Row({ term, value }: { term: string; value: string }) {
  return (
    <div className="flex gap-2">
      <dt className="text-label text-ink-subtle w-14 shrink-0">{term}</dt>
      <dd className="text-body-sm text-ink">{value}</dd>
    </div>
  );
}

function ConfirmedBody({ title }: { title: string }) {
  return (
    <div className="flex flex-col gap-2">
      <p className="text-body text-ink">{title}</p>
      <p className="text-body-sm text-ink-muted">
        캘린더 화면은 아직 없어요. 다음 이슈에서 붙습니다.
      </p>
    </div>
  );
}
