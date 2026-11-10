"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { josa } from "es-hangul";
import { ChevronRight, CircleMinus, Utensils } from "lucide-react";
import { useState } from "react";

import { BottomSheet } from "@/components/ui/bottom-sheet";
import { Button } from "@/components/ui/button";
import { ICON_SIZE, ICON_STROKE } from "@/components/ui/icon";
import { IconButton } from "@/components/ui/icon-button";
import { IconTile } from "@/components/ui/icon-tile";
import { Spinner } from "@/components/ui/spinner";
import { api, isApiError, qk, type HealthSafety } from "@/lib/api";
import { formatDay } from "@/lib/format";

/**
 * 11 · 02 알레르기 구역의 "괜찮았던 식품" (#825 · 서버 PR 3).
 *
 * 05 알레르기 확인 시트에서 "먹어 봤고 괜찮았어요" 로 답해 저장한 식품이다. 그 식품은 다음 제안에서
 * 다시 묻지 않는다. 여기서는 **보고 빼기만** 한다.
 *
 * 🚨 **알레르기 목록과 섞지 않는다.** 서버도 `items` 와 다른 필드(`tolerated`)로 보낸다 — 한 목록에 두면
 *    "잣" 줄이 "잣 알레르기" 로 읽힌다. 알레르기 목록 위의 옅은 한 줄로 두고, 누르면 시트에서 본다.
 *    알레르기가 몇 개든 스크롤 없이 보이고, 없으면 줄도 서지 않는다.
 * 🚨 **"안전해요" 같은 색을 쓰지 않는다.** 평가처럼 읽히지 않게 중립이다. `caution` · `danger` · `approve`
 *    도 쓰지 않는다 — 저장은 05 시트의 게이트 ㉡ 에서 끝났고, 여기서 빼는 것은 보호자가 직접 누르는 삭제라
 *    게이트가 아니라 확인이다 (최상위 §2 실행).
 * 🚨 **고치기가 없다.** 고칠 칸(심각도 · 증상)이 없는 기록이다.
 */
export function ToleratedFoodsRow({ childId, items }: { childId: string; items: HealthSafety[] }) {
  const [open, setOpen] = useState(false);
  /**
   * 🚨 **마지막 식품을 빼면 시트를 닫는다.** 컴포넌트째 `null` 로 빠지면 열린 `<dialog>` 가 닫힘 움직임 없이
   *    사라지고, `open` 이 참으로 남아 나중에 괜찮았던 식품이 다시 생기면 누르지 않은 시트가 저절로 열린다.
   *    그래서 시트는 늘 마운트하고, 줄만 없을 때 숨긴다.
   */
  if (open && items.length === 0) setOpen(false);

  return (
    <>
      {items.length > 0 ? (
        <button
          type="button"
          onClick={() => setOpen(true)}
          className="rounded-field bg-surface-muted ease-standard active:bg-line min-h-touch flex w-full items-center gap-2 px-3 py-2 text-left transition-colors duration-120"
        >
          <span className="text-body-sm text-ink-muted flex-1">괜찮았던 식품 {items.length}개</span>
          <ChevronRight
            aria-hidden
            size={ICON_SIZE.sm}
            strokeWidth={ICON_STROKE}
            className="text-ink-subtle"
          />
        </button>
      ) : null}

      <BottomSheet
        open={open && items.length > 0}
        onClose={() => setOpen(false)}
        title="괜찮았던 식품"
        description="식사 추천을 고를 때 먹어 봤고 괜찮았다고 답한 식품이에요. 이 식품은 다시 묻지 않아요. 빼면 다음 추천에서 다시 물어요."
      >
        <ul className="border-line divide-line rounded-card bg-surface divide-y overflow-hidden border">
          {items.map((item) => (
            <li key={item.id}>
              <ToleratedFoodRow childId={childId} item={item} />
            </li>
          ))}
        </ul>
      </BottomSheet>
    </>
  );
}

function ToleratedFoodRow({ childId, item }: { childId: string; item: HealthSafety }) {
  const queryClient = useQueryClient();
  const [confirming, setConfirming] = useState(false);

  /**
   * 빼기. 🚨 **`Idempotency-Key` 를 달지 않는다** — 알레르기 내리기와 같은 `DELETE` 다(같은 것을 두 번 보내도
   *    결과가 같다). 🚨 **404 는 실패가 아니다** — 다른 보호자가 먼저 뺐다. 목록을 다시 받으면 빠져 있다.
   */
  const remove = useMutation({
    mutationFn: async () => {
      try {
        await api.delete<void>(`/children/${childId}/health-safety/${item.id}`);
      } catch (error) {
        if (!isApiError(error, "not_found")) throw error;
      }
    },
    onSuccess: async () => {
      setConfirming(false);
      // 다음 제안의 사전검사가 바뀐다 (빼면 다시 묻는다). 아이 스코프를 통째로 무효화한다.
      await queryClient.invalidateQueries({ queryKey: qk.child(childId) });
    },
  });

  return (
    <div className="flex flex-col">
      <div className="flex items-start gap-3 px-4 py-3">
        {/* 알레르기 줄과 같은 중립 타일이다. 🚨 `Shield` 가 아니다 — 방패는 "안전해요" 로 읽혀 평가가 된다.
            식품이라는 것만 말한다. */}
        <IconTile icon={Utensils} tone="neutral" />
        <div className="flex min-w-0 flex-1 flex-col gap-1">
          <p className="text-body text-ink">{item.label}</p>
          <p className="text-caption text-ink-subtle">
            {item.created_by ? `${item.created_by.nickname}님이 ` : ""}
            {formatDay(item.updated_at)}에 확인
          </p>
        </div>
        {/* 🚨 내리기와 같은 아이콘이다 — 지우는 것이 아니라 목록에서 빼는 것이다. */}
        <IconButton
          label={`${item.label} 빼기`}
          onClick={() => setConfirming((value) => !value)}
          className="-my-1"
        >
          <CircleMinus aria-hidden size={ICON_SIZE.md} strokeWidth={ICON_STROKE} />
        </IconButton>
      </div>

      {confirming ? (
        <div className="bg-surface-muted border-line flex flex-col gap-3 border-t px-4 py-3">
          {/* 🚨 무엇이 바뀌는지 먼저 말한다. 빼면 **더 묻는 쪽**이라 확인 버튼은 빨강이 아니다. */}
          <p className="text-body-sm text-ink-muted">
            {josa(item.label, "을/를")} 빼면 다음 추천에서 다시 물어요.
          </p>
          {remove.isError ? (
            <p role="status" className="text-body-sm text-ink-muted">
              빼지 못했어요. 아직 그대로 있으니 다시 눌러 주세요.
            </p>
          ) : null}
          <div className="flex gap-2">
            <Button
              variant="secondary"
              size="compact"
              onClick={() => remove.mutate()}
              disabled={remove.isPending}
              className="flex-1"
            >
              {remove.isPending ? <Spinner /> : null}
              {remove.isPending ? "빼는 중이에요" : "뺄게요"}
            </Button>
            <Button
              variant="tertiary"
              size="compact"
              onClick={() => setConfirming(false)}
              disabled={remove.isPending}
              className="flex-1"
            >
              그대로 둘게요
            </Button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
