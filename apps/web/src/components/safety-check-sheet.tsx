"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";

import { Banner } from "@/components/ui/banner";
import { BottomSheet } from "@/components/ui/bottom-sheet";
import { Button } from "@/components/ui/button";
import { CardFailed } from "@/components/ui/card";
import { Chip, ChipRow } from "@/components/ui/chip";
import {
  addHealthSafety,
  newIdempotencyKey,
  qk,
  type CreateHealthSafetyRequest,
  type IdempotencyKey,
  type Precheck,
} from "@/lib/api";

/**
 * 알레르기 사전검사 시트 — **승인 게이트 ㉡ 이 여기 있다** (CLAUDE.md §2 · §3).
 *
 *   ㉡ 건강·알레르기 기록 확정   POST /children/{cid}/health-safety
 *
 * 🚨 **묻는 자리는 제안을 채택할 때다** (#151). 한동안 초안을 만든 뒤(=일정을 만들기로 한 뒤)
 *    승인 시트 안에서 물었는데, 채택과 일정 만들기가 갈리면서 **일정을 안 만들면 영영 안 묻는**
 *    구멍이 생겼다. 알레르기는 캘린더가 아니라 **그 음식을 먹이는 일**에 걸린 위험이다.
 *
 * 🚨 **채택보다 먼저 묻는다.** 채택을 되돌리는 길이 없어서다 — 알레르기가 확인된 제안은
 *    채택 자체를 하지 않고, 그러면 초안도 안 생기고 나중에 막을 것도 남지 않는다.
 *
 * 🚨 **일정 초안 시트와 한 벌로 두지 않는다.** 둘은 다른 게이트(㉡ / ㉠)이고 다른 단계다 —
 *    한 시트에 두면 "알레르기를 확인했다" 와 "캘린더에 넣었다" 가 한 화면에서 섞인다.
 *
 * 🚨 **낙관적 업데이트를 쓰지 않는다.** `onMutate` 로 캐시를 먼저 바꾸면 서버가 확정하기 전에
 *    화면이 이미 확정된 것처럼 보인다 — "되돌릴 수 없는 것은 사람이 승인한다" 가 시각적으로 깨진다.
 * 🚨 **자동 재시도가 꺼져 있다** (`query-client.ts`).
 * 🚨 **재시도할 때 Idempotency-Key 를 새로 만들지 않는다.** 같은 키를 다시 보내는 것이
 *    중복 실행을 막는 유일한 장치다.
 * 🚨 **스크림·ESC 로 닫히지 않는다** (`dismissible={false}`). 확인하다 실수로 닫히면
 *    무엇을 답했는지 모르는 채로 채택이 진행된다.
 */

/** 알레르기 확인은 3지선다다. 🚨 "모르겠어요" 는 "없음" 이 아니다 — 아무것도 저장하지 않는다. */
type SafetyAnswer = "has" | "none" | "unknown";

/** 🚨 보호자의 선택이 아니라 **서버가 저장을 끝냈는가**. 둘을 한 값으로 쓰지 않는다. */
type SafetySaveState = "saving" | "saved" | "failed";

const SAFETY_CHOICES: Array<{ value: SafetyAnswer; label: string }> = [
  { value: "none", label: "먹어봤어요 · 괜찮았어요" },
  { value: "has", label: "알레르기가 있어요" },
  { value: "unknown", label: "모르겠어요" },
];

export function SafetyCheckSheet({
  open,
  childId,
  prechecks,
  onCancel,
  onConfirm,
}: {
  open: boolean;
  childId: string;
  /** 고른 제안에 걸린 검사만 넘어온다. 🚨 화면이 고르지 않는다 — 그릇이 골라서 준다. */
  prechecks: Precheck[];
  /** 그만두기. 🚨 아무것도 채택되지 않는다 (알레르기 기록은 이미 저장된 것만 남는다). */
  onCancel: () => void;
  /**
   * 확인 끝. 🚨 **알레르기가 있는 재료나 화면이 모르는 검사가 걸린 제안 id 를 함께 준다** —
   *    그릇은 그것을 빼고 나머지만 채택한다.
   * 🚨 **`null` 이면 어느 제안인지 모른다는 뜻이다**(서버가 `suggestion_id` 를 안 실었다).
   *    그때는 **전부 막는다** — 알레르기에서 덜 막는 쪽으로 기울 수 없다.
   */
  onConfirm: (blockedSuggestionIds: string[] | null) => void;
}) {
  const queryClient = useQueryClient();

  const [answers, setAnswers] = useState<Record<string, SafetyAnswer>>({});
  /**
   * 재료별 **서버 저장** 상태.
   *
   * 🚨 화면의 "기록에 추가했어요" 는 오직 여기서만 나온다. 예전에는 보호자가 "있어요" 를 누른
   *    순간부터 그 문구가 떴는데, 저장이 실패해도 그대로 남아서 **기록되지 않은 알레르기를
   *    기록됐다고 믿게** 했다 (PR #71 리뷰). 선택은 이 제안을 막을 뿐이고, 다음 제안까지
   *    걸러내는 것은 저장이 끝나야 한다.
   */
  const [saveState, setSaveState] = useState<Record<string, SafetySaveState>>({});

  /**
   * 🚨 **재료 한 건이 사용자 동작 하나다.** 그래서 `useIdempotencyKey()`(동작 하나에 키 하나)가
   *    아니라 재료별 맵이다 — 같은 재료를 다시 저장하면 같은 키가 나가고(재시도), 다른 재료는
   *    다른 키가 나간다. 하나로 묶으면 두 번째 재료가 "같은 키 · 다른 요청" 이라 422 다.
   */
  const safetyKeys = useRef(new Map<string, IdempotencyKey>());

  const ingredientChecks = prechecks.filter((p) => p.code === "unknown_ingredient");
  /**
   * 🚨 **화면이 모르는 `code` 는 묻지 못하니 그 제안을 막는다.** 예전에는 시트만 열리고 아무것도
   *    묻지 않은 채 그 제안이 그대로 채택됐다 — 서버가 새 검사를 보냈는데 화면이 덜 막는 쪽으로
   *    기울면, 알레르기 필터를 규칙이 아니라 화면 버전이 정하게 된다 (최상위 §3).
   */
  const unhandledChecks = prechecks.filter((p) => p.code !== "unknown_ingredient");
  const answeredAll = ingredientChecks.every((p) => answers[p.item] !== undefined);
  /**
   * 🚨 알레르기가 확인된 재료. 저장까지 끝난 재료는 **선택을 바꿔도 계속 막는다** — 기록이 이미
   *    서버에 있는데 화면에서만 풀리면, 알레르기가 등록된 아이에게 그 재료가 든 것을 고를 수 있다.
   */
  const blockedItems = ingredientChecks
    .map((p) => p.item)
    .filter((item) => answers[item] === "has" || saveState[item] === "saved");
  const allergyBlocked = blockedItems.length > 0;
  const blocked = allergyBlocked || unhandledChecks.length > 0;
  /** 막힌 재료가 전부 기록에 남았는가. 이것이 참일 때만 "앞으로도 걸러내요" 라고 말할 수 있다. */
  const recorded = allergyBlocked && blockedItems.every((item) => saveState[item] === "saved");
  const failedItems = blockedItems.filter((item) => saveState[item] === "failed");

  /** 승인 게이트 ㉡ — 보호자가 확인한 값만 들어간다. LLM 이 추론한 값은 여기 오지 않는다 (NF-03). */
  const saveSafety = useMutation({
    mutationFn: (item: string) => {
      const key = safetyKeys.current.get(item) ?? newIdempotencyKey();
      safetyKeys.current.set(item, key);
      const body: CreateHealthSafetyRequest = {
        type: "allergy",
        label: item,
        category: "식품",
        notes: "제안을 고를 때 보호자가 확인",
      };
      return addHealthSafety(childId, body, key);
    },
    onMutate: (item) => setSaveState((prev) => ({ ...prev, [item]: "saving" })),
    onSuccess: (_result, item) => {
      setSaveState((prev) => ({ ...prev, [item]: "saved" }));
      return queryClient.invalidateQueries({ queryKey: qk.healthSafety(childId) });
    },
    onError: (_error, item) => setSaveState((prev) => ({ ...prev, [item]: "failed" })),
  });

  function answer(item: string, value: SafetyAnswer) {
    setAnswers((prev) => ({ ...prev, [item]: value }));
    // 🚨 "있어요" 일 때만 저장한다. "괜찮았어요" 는 알레르기 기록이 아니고,
    //    "모르겠어요" 는 아무것도 저장하지 않는다.
    if (value === "has") saveSafety.mutate(item);
  }

  /** 🚨 알레르기 저장 중에는 닫지 않는다. */
  const busy = Object.values(saveState).some((state) => state === "saving");

  function confirm() {
    if (!blocked) {
      onConfirm([]);
      return;
    }
    const ids = [
      ...ingredientChecks.filter((p) => blockedItems.includes(p.item)),
      ...unhandledChecks,
    ].map((p) => p.suggestion_id);
    // 🚨 하나라도 어느 제안인지 모르면 **전부 막는다** (위 `onConfirm` 머리말).
    onConfirm(ids.some((id) => id === undefined) ? null : (ids as string[]));
  }

  return (
    <BottomSheet
      open={open}
      onClose={onCancel}
      dismissible={false}
      title="확인해 주세요"
      description="고르기 전에 알레르기부터 확인해요."
      footer={
        <div className="flex flex-col gap-2">
          {/*
           * 🚨 **이 버튼은 승인 게이트가 아니다.** 게이트 ㉡ 는 위에서 "알레르기가 있어요" 를
           *    고른 그 순간 이미 지나갔다(저장까지 끝난다) — 여기서 하는 일은 **고르기를
           *    이어 가는 것**뿐이라 `btn-approve` 도 `caution` 도 쓰지 않는다.
           */}
          <Button block disabled={!answeredAll || busy} aria-busy={busy} onClick={confirm}>
            {allergyBlocked
              ? "알레르기가 있는 건 빼고 고를게요"
              : blocked
                ? "확인하지 못한 건 빼고 고를게요"
                : "확인했어요"}
          </Button>
          <Button variant="tertiary" block onClick={onCancel} disabled={busy}>
            그만두기
          </Button>
        </div>
      }
    >
      <div className="flex flex-col gap-4">
        {unhandledChecks.length > 0 ? (
          <Banner tone="danger" title="여기서 확인할 수 없는 항목이 있어요">
            {unhandledChecks.map((p) => p.item).join(", ")} — 이 항목이 걸린 제안은 고르지 않아요.
          </Banner>
        ) : null}

        {allergyBlocked ? (
          // 🚨 두 문구를 가르는 것은 보호자의 선택이 아니라 **서버 저장 결과**다.
          //    저장 전에 "앞으로도 걸러내요" 라고 말하면, 저장이 실패한 채로 닫은 보호자가
          //    다음 제안에서도 걸러진다고 믿는다 (PR #71 리뷰).
          recorded ? (
            <Banner tone="danger" title="알레르기 기록에 추가했어요">
              이 재료가 들어간 제안은 고르지 않아요. 앞으로의 식사 제안에서도 걸러내요.
            </Banner>
          ) : (
            <Banner tone="danger" title="아직 기록에는 저장되지 않았어요">
              이 재료가 들어간 제안은 고르지 않아요. 다만 기록에 저장되기 전까지는 다음 제안에서
              걸러내지 못해요.
            </Banner>
          )
        ) : ingredientChecks.length > 0 ? (
          <Banner tone="caution" title="처음 보는 재료가 있어요">
            아이 알레르기 기록에 없는 재료예요. 보호자가 확인해 주셔야 고를 수 있어요.
          </Banner>
        ) : null}

        {ingredientChecks.map((precheck) => (
          <SafetyCheck
            key={precheck.item}
            precheck={precheck}
            value={answers[precheck.item]}
            onAnswer={(value) => answer(precheck.item, value)}
            disabled={saveState[precheck.item] === "saving"}
          />
        ))}

        {failedItems.length > 0 ? (
          // 🚨 예전에는 `saveSafety.reset()` 을 부르는 "다시 고르기" 였다 — 실패 안내만 지우고
          //    저장은 다시 시도하지 않아서, 화면에서 실패가 사라진 채로 기록이 비어 있었다.
          <CardFailed>
            <p>
              {failedItems.join(", ")} — 알레르기 기록에 저장하지 못했어요. 이 제안은 고르지 않지만,
              저장되기 전까지는 다음 제안에서 걸러내지 못해요.
            </p>
            <Button
              variant="tertiary"
              size="compact"
              className="mt-3"
              disabled={busy}
              // 🚨 재료별 키를 그대로 다시 쓴다 (`safetyKeys`). 새 키를 만들면 이미 저장된
              //    재료가 한 번 더 들어간다.
              onClick={() => failedItems.forEach((item) => saveSafety.mutate(item))}
            >
              다시 시도
            </Button>
          </CardFailed>
        ) : null}
      </div>
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
