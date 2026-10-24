"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { Checkbox } from "@/components/ui/checkbox";
import { useToast } from "@/components/ui/toast";
import {
  api,
  qk,
  type CheckDoseRequest,
  type MedicationDose,
  type MedicationsResponse,
} from "@/lib/api";
import { formatTimeOfDay } from "@/lib/format";
import { createSerialQueue } from "@/lib/serial-queue";

/**
 * 회차 한 줄 — **11-3 복약과 03 홈 "오늘 먹일 약" 이 같이 쓴다** (#563 리뷰). 같은 쿼리 키(`qk.medications`)를
 * 고치므로 한쪽에서 체크하면 다른 쪽도 같은 값이다.
 *
 * 🚨 **낙관적 업데이트에 줄을 세운다** — 09 준비물 체크와 같은 이유다 (`calendar-day.tsx` 의 `ItemRow`
 * · `lib/serial-queue.ts`). 체크 → 해제를 빠르게 누르면 응답이 역전돼 마지막 선택이 아닌 값이 남을 수 있다.
 * 되돌릴 수 있는 곳이라 응답을 기다리지 않는다 (게이트에는 낙관적 업데이트를 쓰지 않는다).
 */
export function DoseCheck({ childId, dose }: { childId: string; dose: MedicationDose }) {
  const queryClient = useQueryClient();
  const toast = useToast();
  const key = qk.medications(childId);
  const [queue] = useState(createSerialQueue);

  const toggle = useMutation({
    mutationFn: (checked: boolean) =>
      queue.run(() =>
        api.put<MedicationDose>(`/children/${childId}/medication-doses/${dose.id}/check`, {
          checked,
        } satisfies CheckDoseRequest),
      ),
    onMutate: async (checked) => {
      await queryClient.cancelQueries({ queryKey: key });
      const previous = queryClient.getQueryData<MedicationsResponse>(key);
      queryClient.setQueryData<MedicationsResponse>(key, (current) =>
        current
          ? {
              ...current,
              courses: current.courses.map((course) => ({
                ...course,
                doses: course.doses.map((each) =>
                  each.id === dose.id
                    ? // 체크한 시각은 서버가 정한다 — 응답 전까지 잠시 지금 시각을 보여 줄 뿐이다.
                      { ...each, checked_at: checked ? new Date().toISOString() : null }
                    : each,
                ),
              })),
            }
          : current,
      );
      return { previous };
    },
    // 🚨 되돌리기만 하면 조용하다 — 체크가 풀린 이유가 어디에도 없어 부모는 잘못 눌렀다고 생각한다.
    //    체크 칸 옆에는 문장 자리가 없어서 토스트가 받는다 (`calendar-day.tsx` 와 같은 판단).
    onError: (_error, _variables, context) => {
      if (queue.pending === 0 && context?.previous) queryClient.setQueryData(key, context.previous);
      toast.show("복용 체크를 저장하지 못했어요. 잠시 뒤에 다시 눌러주세요.");
    },
    onSettled: () => {
      if (queue.pending === 0) void queryClient.invalidateQueries({ queryKey: key });
    },
  });

  // 🚨 체크할 수 있는지는 서버가 정한다 — 화면이 오늘과 견주지 않는다.
  if (!dose.checkable) {
    return (
      <div className="min-h-touch flex items-center justify-between gap-3 py-2">
        <span className="text-body text-ink-muted">{dose.scheduled_time}</span>
        <span className="text-caption text-ink-subtle">아직 체크할 수 없어요</span>
      </div>
    );
  }

  return (
    <Checkbox
      checked={dose.checked_at !== null}
      onChange={(checked) => toggle.mutate(checked)}
      label={dose.scheduled_time}
      description={dose.checked_at ? `${formatTimeOfDay(dose.checked_at)}에 체크했어요` : undefined}
    />
  );
}
