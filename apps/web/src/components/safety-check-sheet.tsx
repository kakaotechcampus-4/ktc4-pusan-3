"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { josa } from "es-hangul";
import { useRef, useState } from "react";

import { Banner } from "@/components/ui/banner";
import { BottomSheet } from "@/components/ui/bottom-sheet";
import { Button } from "@/components/ui/button";
import { CardFailed } from "@/components/ui/card";
import { Chip, ChipRow } from "@/components/ui/chip";
import { Spinner } from "@/components/ui/spinner";
import {
  addHealthSafety,
  isApiError,
  newIdempotencyKey,
  qk,
  type CreateHealthSafetyRequest,
  type IdempotencyKey,
  type Precheck,
} from "@/lib/api";

/**
 * 알레르기 사전검사 시트 — **승인 게이트 ㉡ 이 여기 있다** (CLAUDE.md §2 · §3).
 * 규칙 원문은 `docs/web/event-draft-ui-v1.md` §3-8 이다.
 *
 *   ㉡ 건강·알레르기 기록 확정   POST /children/{cid}/health-safety
 *
 * 🚨 **게이트는 아래 버튼이다** (#823). 칩은 답만 고른다. 한동안 "알레르기가 있어요" 칩을 누르는
 *    순간 저장했는데, 잘못 눌러도 시트 안에서 되돌릴 수 없었고, 칩을 바꿔도 저장된 식품은 계속 막혀서
 *    칩과 배너와 버튼이 서로 다른 말을 했다. 게이트를 칩에서 버튼으로 옮긴 것이라 게이트 수는 그대로다.
 *
 * 🚨 **묻는 자리는 제안을 채택할 때다** (#151). 알레르기는 캘린더가 아니라 **그 음식을 먹이는 일**에
 *    걸린 위험이라, 일정을 만들기로 한 뒤에 물으면 일정을 안 만드는 보호자에게는 영영 안 묻는다.
 * 🚨 **채택보다 먼저 묻는다.** 채택을 되돌리는 길이 없어서다 — 알레르기가 확인된 제안은
 *    채택 자체를 하지 않는다.
 * 🚨 **저장이 하나라도 실패하면 채택으로 넘어가지 않는다.** 시트를 연 채로 두고 실패한 것만 다시
 *    보낸다. 저장되지 않은 알레르기를 두고 채택이 지나가면, 보호자는 다음 제안에서도 걸러진다고 믿는다.
 * 🚨 **일정 초안 시트와 한 벌로 두지 않는다.** 둘은 다른 게이트(㉡ / ㉠)이고 다른 단계다.
 * 🚨 **낙관적 업데이트를 쓰지 않는다** · **자동 재시도가 꺼져 있다** (`query-client.ts`) ·
 *    **재시도할 때 Idempotency-Key 를 새로 만들지 않는다.**
 * 🚨 **스크림·ESC 로 닫히지 않는다** (`dismissible={false}`).
 */

/** 알레르기 확인은 3지선다다. 🚨 "모르겠어요" 는 "없음" 이 아니다 — 아무것도 저장하지 않는다. */
type SafetyAnswer = "has" | "none" | "unknown";

/** 🚨 보호자의 선택이 아니라 **서버가 저장을 끝냈는가**. 둘을 한 값으로 쓰지 않는다. */
type SafetySaveState = "saving" | "saved" | "failed";

const SAFETY_CHOICES: Array<{ value: SafetyAnswer; label: string }> = [
  { value: "none", label: "먹어 봤고 괜찮았어요" },
  { value: "has", label: "알레르기가 있어요" },
  { value: "unknown", label: "모르겠어요" },
];

/** 확인이 끝났다. 그릇(05)이 이것으로 채택할 것을 고른다. */
export interface SafetyResult {
  /**
   * 🚨 **알레르기가 있는 식품이나 화면이 모르는 검사가 걸린 제안 id.** 그릇은 그것을 빼고 나머지만 채택한다.
   * 🚨 **`null` 이면 어느 제안인지 모른다는 뜻이다**(서버가 `suggestion_id` 를 안 실었다).
   *    그때는 **전부 막는다** — 알레르기에서 덜 막는 쪽으로 기울 수 없다.
   */
  blockedSuggestionIds: string[] | null;
  /**
   * 고른 것을 전부 막은 알레르기 식품 이름. 그릇이 "OO이 들어간 제안은 고를 수 없어요" 에 쓴다.
   * 🚨 **알레르기 하나로 전부 막혔을 때만 채운다.** 화면이 모르는 검사가 섞였거나 어느 제안인지 몰라 전부
   *    막았으면 빈 배열이다 — 알레르기 식품이 없는 제안까지 그 식품 때문이라고 말하게 된다.
   */
  allergyItems: string[];
}

export function SafetyCheckSheet({
  open,
  childId,
  prechecks,
  selectedIds,
  onCancel,
  onConfirm,
}: {
  open: boolean;
  childId: string;
  /** 고른 제안에 걸린 검사만 넘어온다. 🚨 화면이 고르지 않는다 — 그릇이 골라서 준다. */
  prechecks: Precheck[];
  /** 고른 제안 전부. 막고 나서 고를 것이 남는지 알아야 버튼과 배너가 맞는 말을 한다. */
  selectedIds: string[];
  /** 그만두기. 🚨 아무것도 채택되지 않는다 (알레르기 기록은 이미 저장된 것만 남는다). */
  onCancel: () => void;
  onConfirm: (result: SafetyResult) => void;
}) {
  const queryClient = useQueryClient();

  const [answers, setAnswers] = useState<Record<string, SafetyAnswer>>({});
  /**
   * 식품별 **서버 저장** 상태.
   *
   * 🚨 화면의 "추가했어요" 는 오직 여기서만 나온다. 보호자의 선택은 이 제안을 막을 뿐이고,
   *    다음 제안까지 걸러내는 것은 저장이 끝나야 한다 (PR #71 리뷰).
   * 🚨 시트는 닫혀도 내려가지 않는다(05 가 늘 그린다). 그래서 저장된 식품은 다시 열어도 저장된 채로
   *    서고 칩이 잠긴다 — 서버에 이미 있는 알레르기를 화면에서 풀 수 없다.
   */
  const [saveState, setSaveState] = useState<Record<string, SafetySaveState>>({});

  /**
   * 🚨 **식품 한 건이 사용자 동작 하나다.** 그래서 `useIdempotencyKey()`(동작 하나에 키 하나)가
   *    아니라 식품별 맵이다 — 같은 식품을 다시 저장하면 같은 키가 나가고(재시도), 다른 식품은
   *    다른 키가 나간다. 하나로 묶으면 두 번째 식품이 "같은 키 · 다른 요청" 이라 422 다.
   */
  const safetyKeys = useRef(new Map<string, IdempotencyKey>());

  const ingredientChecks = prechecks.filter((p) => p.code === "unknown_ingredient");
  /**
   * 🚨 **화면이 모르는 `code` 는 묻지 못하니 그 제안을 막는다.** 서버가 새 검사를 보냈는데 화면이 덜 막는
   *    쪽으로 기울면, 알레르기 필터를 규칙이 아니라 화면 버전이 정하게 된다 (최상위 §3).
   */
  const unhandledChecks = prechecks.filter((p) => p.code !== "unknown_ingredient");
  /**
   * 묻는 식품. 🚨 **같은 식품은 한 장이다** (#823). 묻는 것은 제안이 아니라 식품이라, 같은 식품이 든
   *    제안을 둘 고르면 같은 질문이 두 번 서고 React key 도 겹쳤다. 막을 때는 그 식품이 든 제안을
   *    전부 막는다 (아래 `blockedChecks`).
   */
  const items = [...new Set(ingredientChecks.map((p) => p.item))];
  const answeredAll = items.every((item) => answers[item] !== undefined);

  const allergyItems = items.filter((item) => answers[item] === "has");
  /** 버튼을 누르면 저장할 식품. 저장이 끝난 것은 다시 보내지 않는다. */
  const toSave = allergyItems.filter((item) => saveState[item] !== "saved");
  const failedItems = allergyItems.filter((item) => saveState[item] === "failed");
  const savedItems = allergyItems.filter((item) => saveState[item] === "saved");
  /** 알레르기로 답한 식품이 전부 기록에 남았는가. 이것이 참일 때만 "추가했어요" 라고 말할 수 있다. */
  const recorded = allergyItems.length > 0 && toSave.length === 0;
  const unknownLeft = items.some((item) => answers[item] === "unknown");

  const blockedChecks = [
    ...ingredientChecks.filter((p) => allergyItems.includes(p.item)),
    ...unhandledChecks,
  ];
  // 🚨 하나라도 어느 제안인지 모르면 **전부 막는다** (`SafetyResult` 의 머리말).
  const blockedSuggestionIds = blockedChecks.some((p) => p.suggestion_id === undefined)
    ? null
    : [...new Set(blockedChecks.map((p) => p.suggestion_id as string))];
  const remaining =
    blockedSuggestionIds === null
      ? 0
      : selectedIds.filter((id) => !blockedSuggestionIds.includes(id)).length;
  /** 막고 나면 고를 것이 없다. 버튼은 고르기가 아니라 저장만 하고, 그릇이 목록에서 안내한다. */
  const allBlocked = blockedChecks.length > 0 && remaining === 0;
  /**
   * 🚨 **전부 막힌 이유가 알레르기 하나일 때만** "고른 제안에 모두 알레르기 식품이 들어가 있어서" 라고 말한다.
   *    화면이 모르는 검사가 섞였거나 어느 제안인지 몰라 전부 막은 것이면 알레르기 식품이 없는 제안도 막혔다.
   */
  const blockedByAllergyOnly =
    unhandledChecks.length === 0 &&
    blockedSuggestionIds !== null &&
    selectedIds.every((id) =>
      ingredientChecks.some((p) => p.suggestion_id === id && allergyItems.includes(p.item)),
    );

  /** 승인 게이트 ㉡ — 보호자가 확인한 값만 들어간다. LLM 이 추론한 값은 여기 오지 않는다 (NF-03). */
  const saveSafety = useMutation({
    mutationFn: (item: string) => {
      const key = safetyKeys.current.get(item) ?? newIdempotencyKey();
      safetyKeys.current.set(item, key);
      const body: CreateHealthSafetyRequest = {
        type: "allergy",
        label: item,
        notes: "제안을 고를 때 보호자가 확인",
      };
      return addHealthSafety(childId, body, key);
    },
    onMutate: (item) => setSaveState((prev) => ({ ...prev, [item]: "saving" })),
    onSuccess: (_result, item) => {
      setSaveState((prev) => ({ ...prev, [item]: "saved" }));
      return queryClient.invalidateQueries({ queryKey: qk.healthSafety(childId) });
    },
    onError: (error, item) => {
      // 🚨 **409 `already_exists` 는 실패가 아니다** (#369). 그 식품이 이미 알레르기 기록에 있다는
      //    뜻이라(다른 보호자가 먼저 등록했거나, 앞서 등록한 응답만 못 받았다) 다음 제안에서도
      //    걸러진다. 실패로 두면 "다시 시도" 가 서는데, 다시 눌러도 같은 409 다.
      const saved = isApiError(error, "already_exists");
      setSaveState((prev) => ({ ...prev, [item]: saved ? "saved" : "failed" }));
      if (saved) void queryClient.invalidateQueries({ queryKey: qk.healthSafety(childId) });
    },
  });

  /** 🚨 알레르기 저장 중에는 닫지도, 답을 바꾸지도 않는다. */
  const busy = Object.values(saveState).some((state) => state === "saving");

  /**
   * 🚨 **저장이 먼저, 채택이 그다음이다.** 하나라도 저장되지 않으면 여기서 멈춘다 — 실패한 식품은
   *    이 버튼을 다시 누를 때 같은 키로 다시 보낸다(실패 안내는 버튼 바로 위). `already_exists` 는 저장된 것으로 친다(`onError`).
   */
  async function submit() {
    const results = await Promise.all(
      toSave.map((item) =>
        saveSafety.mutateAsync(item).then(
          () => true,
          (error: unknown) => isApiError(error, "already_exists"),
        ),
      ),
    );
    if (!results.every(Boolean)) return;
    onConfirm({ blockedSuggestionIds, allergyItems: blockedByAllergyOnly ? allergyItems : [] });
  }

  const allergyNames = allergyItems.join(", ");

  return (
    <BottomSheet
      open={open}
      onClose={onCancel}
      dismissible={false}
      title="확인해 주세요"
      // 🚨 저장되는 것은 "알레르기가 있어요" 뿐이다. "고른 내용이 저장돼요" 라고 하면 괜찮았어요 · 모르겠어요도
      //    기록에 남는다고 읽힌다 (괜찮았던 식품 저장은 #825).
      description="고르기 전에 알레르기부터 확인해요. 알레르기가 있다고 답한 식품만 아래 버튼을 누를 때 기록에 저장돼요."
      footer={
        <div className="flex flex-col gap-2">
          {/* 🚨 **실패 안내는 버튼 바로 위다** (#823 리뷰). 본문 맨 아래에 두면 고정된 아래 영역에 깔려서,
              누른 뒤 화면에 아무 변화가 없는 것처럼 보였다. 다시 시도는 아래 버튼 하나다 — 같은 키로
              실패한 식품만 다시 보내고, 다 저장되면 고르기로 넘어간다. 실패를 빨강으로 칠하지 않는다. */}
          {failedItems.length > 0 && !busy ? (
            <CardFailed>
              {josa(failedItems.join(", "), "을/를")} 알레르기 기록에 저장하지 못했어요. 아직
              아무것도 고르지 않았어요.
              {savedItems.length > 0
                ? ` ${josa(savedItems.join(", "), "은/는")} 저장했어요.`
                : null}
            </CardFailed>
          ) : !answeredAll ? (
            <p className="text-body-sm text-ink-muted">식품마다 하나씩 골라 주세요</p>
          ) : unknownLeft && !allBlocked ? (
            <p className="text-body-sm text-ink-muted">아직 모르는 식품이 포함돼 있어요</p>
          ) : null}
          {/*
           * 🚨 **이 버튼이 승인 게이트 ㉡ 다** — 저장할 알레르기가 있을 때만. 그때만 `approve` 표식을
           *    쓰고, 저장할 것이 없으면 고르기를 이어 가는 버튼이라 표식이 없다.
           */}
          <Button
            variant={toSave.length > 0 ? "approve" : "primary"}
            block
            disabled={!answeredAll || busy}
            aria-busy={busy}
            onClick={() => void submit()}
          >
            {busy ? <Spinner /> : null}
            {busy
              ? "저장하는 중이에요"
              : !answeredAll
                ? "이대로 고르기"
                : toSave.length > 0
                  ? // 🚨 식품별 키를 그대로 다시 쓴다 (`safetyKeys`). 새 키를 만들면 이미 저장된 식품이 한 번 더 들어간다.
                    failedItems.length > 0
                    ? allBlocked
                      ? "다시 저장하기"
                      : "다시 저장하고 나머지 고르기"
                    : allBlocked
                      ? "알레르기 저장하기"
                      : "저장하고 나머지 고르기"
                  : allBlocked
                    ? "확인했어요"
                    : blockedChecks.length > 0
                      ? "나머지 고르기"
                      : "이대로 고르기"}
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
            {josa(unhandledChecks.map((p) => p.item).join(", "), "은/는")} 여기서 확인할 수 없어서,
            이 항목이 걸린 제안은 고르지 않아요.
          </Banner>
        ) : null}

        {allergyItems.length > 0 ? (
          // 🚨 "추가했어요" 를 가르는 것은 보호자의 선택이 아니라 **서버 저장 결과**다 (PR #71 리뷰).
          //    누르기 전에는 "추가해요" — 버튼을 누르면 일어날 일을 말한다.
          <Banner
            tone="danger"
            title={`${josa(allergyNames, "을/를")} 알레르기 기록에 ${recorded ? "추가했어요" : "추가해요"}`}
          >
            {allBlocked && blockedByAllergyOnly
              ? "고른 제안에 모두 알레르기 식품이 들어가 있어서 이번에는 고를 수 있는 제안이 없어요. 앞으로의 제안에서도 빼요."
              : `${josa(allergyNames, "이/가")} 들어간 제안은 고르지 않고, 앞으로의 제안에서도 빼요.`}
          </Banner>
        ) : items.length > 0 && !answeredAll ? (
          <Banner tone="caution" title="처음 보는 식품이 있어요">
            아이 알레르기 기록에 없는 식품이에요. 먹어본 적이 있는지 알려 주세요.
          </Banner>
        ) : null}

        {items.map((item) => (
          <SafetyCheck
            key={item}
            item={item}
            suggestionCount={
              new Set(
                ingredientChecks
                  .filter((p) => p.item === item && p.suggestion_id !== undefined)
                  .map((p) => p.suggestion_id),
              ).size
            }
            value={answers[item]}
            // 🚨 저장 중에는 답을 바꾸지 않는다. 칩을 `disabled` 로 두면 고른 칩까지 회색이 돼서
            //    무엇을 골랐는지 사라지므로(`Chip` 은 비활성이 선택보다 이긴다), 누름만 무시한다.
            onAnswer={(value) => {
              if (!busy) setAnswers((prev) => ({ ...prev, [item]: value }));
            }}
            saved={saveState[item] === "saved"}
          />
        ))}
      </div>
    </BottomSheet>
  );
}

function SafetyCheck({
  item,
  suggestionCount,
  value,
  onAnswer,
  saved,
}: {
  item: string;
  /** 이 식품이 든 고른 제안 수. 둘 이상일 때만 말한다. */
  suggestionCount: number;
  value: SafetyAnswer | undefined;
  onAnswer: (value: SafetyAnswer) => void;
  /**
   * 🚨 알레르기 기록에 저장이 끝났다. 서버에 있는 기록을 화면에서 풀 수 없어 답을 바꾸지 못하게 한다.
   *    칩을 `disabled` 로 잠그면 세 칩이 다 같은 회색이라 어느 답이 저장됐는지 사라진다 — 칩 대신 답을 글자로 둔다.
   */
  saved: boolean;
}) {
  return (
    <section className="border-line rounded-card bg-surface border p-4">
      {/* 🚨 제목은 식품 이름만이다. 서버의 `note`("알레르기 기록에 없음")는 목록을 받은 순간의 고정값이라
          저장한 뒤에도 그대로 남았다 (#823). */}
      <p className="text-section text-ink">{item}</p>
      {suggestionCount > 1 ? (
        <p className="text-caption text-ink-subtle mt-1">
          고른 제안 {suggestionCount}개에 들어 있어요
        </p>
      ) : null}
      {saved ? (
        <p className="text-body-sm text-ink mt-2">
          알레르기가 있다고 답했어요. 알레르기 기록에 저장했어요.
        </p>
      ) : (
        <>
          <p className="text-body-sm text-ink-muted mt-1">이 식품을 먹어본 적이 있나요?</p>
          <ChipRow>
            {SAFETY_CHOICES.map((choice) => (
              <Chip
                key={choice.value}
                selected={value === choice.value}
                onClick={() => onAnswer(choice.value)}
              >
                {choice.label}
              </Chip>
            ))}
          </ChipRow>
        </>
      )}
    </section>
  );
}
