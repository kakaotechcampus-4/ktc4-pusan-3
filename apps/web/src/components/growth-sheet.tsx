"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { BottomSheet } from "@/components/ui/bottom-sheet";
import { Button } from "@/components/ui/button";
import { DateField } from "@/components/ui/date-field";
import { Spinner } from "@/components/ui/spinner";
import { TextInput } from "@/components/ui/text-input";
import { EARLIEST_BIRTH_DATE, toToday } from "@/lib/date-bounds";
import { api, qk, type GrowthLog } from "@/lib/api";

/**
 * 새 측정 기록. 🚨 **승인 게이트가 아니다** — `caution` 도 `btn-approve` 도 쓰지 않고,
 * 스크림 탭으로 닫힌다 (`dismissible` 기본값). 잘못 적으면 그 줄을 지우면 된다 (최상위 §2).
 *
 * 🚨 **11 프로필과 11-1 상세가 같은 시트를 쓴다.** 두 화면에 각자 폼을 두면 한쪽에만 붙은
 *    검증이 생기고(실제로 "둘 다 비어 있으면 막는다" 가 그런 규칙이다), 화면마다 다른 것을
 *    막게 된다. 열리는 자리가 둘일 뿐 **적는 방법은 하나**다.
 */
export function GrowthSheet({
  open,
  onClose,
  childId,
}: {
  open: boolean;
  onClose: () => void;
  childId: string;
}) {
  const queryClient = useQueryClient();

  const [measuredOn, setMeasuredOn] = useState(toToday);
  const [height, setHeight] = useState("");
  const [weight, setWeight] = useState("");
  /**
   * 🚨 **사유를 입력에 붙인다.** 시트 아래 떠 있는 문단으로 두면 보조기술이 그 문구를 어느
   *    칸의 문제인지 잇지 못한다 — `TextInput` 의 `error` 는 `aria-invalid` 와
   *    `aria-describedby` 를 함께 걸어 준다 (디자인 시스템 §7 입력).
   *    "둘 중 하나는 적어주세요" 처럼 칸 하나에 안 붙는 사유는 **첫 칸**이 받는다.
   */
  const [errors, setErrors] = useState<{ height?: string; weight?: string }>({});

  const save = useMutation({
    mutationFn: (body: { measured_on: string; height_cm?: number; weight_kg?: number }) =>
      api.post<{ log: GrowthLog }>(`/children/${childId}/growth`, body),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: qk.growth(childId) });
      reset();
      onClose();
    },
  });

  function reset() {
    setMeasuredOn(toToday());
    setHeight("");
    setWeight("");
    setErrors({});
    save.reset();
  }

  function submit() {
    const h = parseMeasurement(height);
    const w = parseMeasurement(weight);

    if (h === "invalid" || w === "invalid") {
      setErrors({
        ...(h === "invalid" ? { height: "숫자로 적어주세요" } : {}),
        ...(w === "invalid" ? { weight: "숫자로 적어주세요" } : {}),
      });
      return;
    }
    // 🚨 한쪽만 재고 오는 날이 있다. 둘 다 비어 있을 때만 막는다.
    if (h === null && w === null) {
      setErrors({ height: "키나 몸무게 중 하나는 적어주세요" });
      return;
    }
    setErrors({});

    save.mutate({
      measured_on: measuredOn,
      ...(h !== null ? { height_cm: h } : {}),
      ...(w !== null ? { weight_kg: w } : {}),
    });
  }

  return (
    <BottomSheet
      open={open}
      onClose={() => {
        reset();
        onClose();
      }}
      title="새로 재서 적기"
      description="둘 중 하나만 적어도 괜찮아요."
      footer={
        <Button block onClick={submit} disabled={save.isPending}>
          {save.isPending ? <Spinner /> : null}
          {save.isPending ? "적는 중이에요" : "적어 두기"}
        </Button>
      }
    >
      <div className="flex flex-col gap-4">
        <DateField
          label="잰 날"
          value={measuredOn}
          onChange={setMeasuredOn}
          fromDate={EARLIEST_BIRTH_DATE}
          toDate={new Date()}
        />

        {/* ⚠️ 입력을 16px 미만으로 내리지 않는다 — iOS 웹뷰가 화면을 확대하고 그대로 남는다
            (`TextInput` 주석). `inputMode="decimal"` 로 숫자 키패드만 띄운다. */}
        <TextInput
          label="키 · cm"
          value={height}
          onChange={(e) => setHeight(e.target.value)}
          error={errors.height}
          inputMode="decimal"
          autoComplete="off"
          placeholder="104.2"
        />

        <TextInput
          label="몸무게 · kg"
          value={weight}
          onChange={(e) => setWeight(e.target.value)}
          error={errors.weight}
          inputMode="decimal"
          autoComplete="off"
          placeholder="17.1"
        />

        {save.isError ? (
          <p role="status" className="text-body-sm text-ink-muted">
            적지 못했어요. 저장된 것은 없으니 다시 눌러 주세요.
          </p>
        ) : null}
      </div>
    </BottomSheet>
  );
}

/**
 * 빈 칸은 `null`, 숫자가 아니면 `"invalid"`.
 * 🚨 잘못 적은 값을 조용히 0 이나 `NaN` 으로 넘기지 않는다 — 아이 몸무게가 0kg 으로 쌓인다.
 */
function parseMeasurement(value: string): number | null | "invalid" {
  const trimmed = value.trim();
  if (!trimmed) return null;
  const parsed = Number(trimmed);
  if (!Number.isFinite(parsed) || parsed <= 0) return "invalid";
  return parsed;
}
