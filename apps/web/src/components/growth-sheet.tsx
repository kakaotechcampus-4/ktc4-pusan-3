"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { BottomSheet } from "@/components/ui/bottom-sheet";
import { Button } from "@/components/ui/button";
import { DateField } from "@/components/ui/date-field";
import { Spinner } from "@/components/ui/spinner";
import { TextInput } from "@/components/ui/text-input";
import { EARLIEST_BIRTH_DATE, toToday } from "@/lib/date-bounds";
import {
  api,
  qk,
  type GrowthLog,
  type UpdateGrowthLogRequest,
  type UpdateGrowthLogResponse,
} from "@/lib/api";

/**
 * 측정 기록을 **적거나 고치는** 시트.
 *
 * 🚨 **승인 게이트가 아니다** — `caution` 도 `btn-approve` 도 쓰지 않고, 스크림 탭으로 닫힌다
 *    (`dismissible` 기본값). 잘못 적으면 고치거나 그 줄을 지우면 된다 (최상위 §2).
 *
 * 🚨 **11 프로필과 11-1 상세가 같은 시트를 쓴다.** 두 화면에 각자 폼을 두면 한쪽에만 붙은
 *    검증이 생기고(실제로 "둘 다 비어 있으면 막는다" 가 그런 규칙이다), 화면마다 다른 것을
 *    막게 된다. 열리는 자리가 둘일 뿐 **적는 방법은 하나**다.
 *
 * 🚨 **고치기도 같은 시트다** (`log` 를 넘기면 고치기). 11 알레르기가 등록과 고치기에 같은
 *    시트를 쓰는 것과 같은 판단이다 — 적는 칸이 같은데 화면을 둘로 두면 한쪽 검증만 바뀐다.
 *
 * 🚨 **고칠 때 못 바꾸는 칸이 없다.** 알레르기는 종류·이름이 기록의 정체라 잠갔지만
 *    (`UNIQUE(child_id, type, label)` 이 흔들린다), 측정 기록에는 그런 제약이 없고 잰 날짜를
 *    잘못 고르는 것이 흔한 실수다. 되돌릴 수 있는 값이라 잠글 이유가 없다.
 */
export function GrowthSheet({
  open,
  onClose,
  childId,
  log,
}: {
  open: boolean;
  onClose: () => void;
  childId: string;
  /**
   * 넘기면 **고치기**, 없으면 **새로 적기**다.
   * 🚨 호출부가 `key={log.id}` 로 새로 세운다 — 폼이 `useState` 로 값을 들고 있어서 같은
   *    인스턴스를 재사용하면 앞 줄의 값이 남는다 (11 프로필의 다른 시트들과 같은 처리).
   */
  log?: GrowthLog;
}) {
  const queryClient = useQueryClient();
  const editing = log !== undefined;

  const [measuredOn, setMeasuredOn] = useState(() => log?.measured_on ?? toToday());
  /** 🚨 `null`(안 잼)은 빈 칸이다 — `0` 으로 채우면 그날 0cm 였다는 말이 된다. */
  const [height, setHeight] = useState(() => (log?.height_cm != null ? String(log.height_cm) : ""));
  const [weight, setWeight] = useState(() => (log?.weight_kg != null ? String(log.weight_kg) : ""));
  /**
   * 🚨 **사유를 입력에 붙인다.** 시트 아래 떠 있는 문단으로 두면 보조기술이 그 문구를 어느
   *    칸의 문제인지 잇지 못한다 — `TextInput` 의 `error` 는 `aria-invalid` 와
   *    `aria-describedby` 를 함께 걸어 준다 (디자인 시스템 §7 입력).
   *    "둘 중 하나는 적어주세요" 처럼 칸 하나에 안 붙는 사유는 **첫 칸**이 받는다.
   */
  const [errors, setErrors] = useState<{ height?: string; weight?: string }>({});

  const save = useMutation({
    mutationFn: (body: UpdateGrowthLogRequest) =>
      /**
       * 🚨 **고칠 때는 바뀐 필드만 보낸다** (아래 `submit`). 안 고친 것까지 올리면 두 보호자가
       *    같은 화면을 열어 뒀을 때 나중 저장이 남의 수정을 덮는다.
       * 🚨 `Idempotency-Key` 를 붙이지 않는다 — 되돌릴 수 없는 5개에 이 경로가 없다.
       */
      editing
        ? api.patch<UpdateGrowthLogResponse>(`/children/${childId}/growth/${log.id}`, body)
        : api.post<{ log: GrowthLog }>(`/children/${childId}/growth`, body),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: qk.growth(childId) });
      reset();
      onClose();
    },
  });

  /** 🚨 고치기는 **넘겨받은 값**으로 돌아간다. 새로 적기처럼 비우면 고치던 줄이 빈 폼이 된다. */
  function reset() {
    setMeasuredOn(log?.measured_on ?? toToday());
    setHeight(log?.height_cm != null ? String(log.height_cm) : "");
    setWeight(log?.weight_kg != null ? String(log.weight_kg) : "");
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

    if (!editing) {
      save.mutate({
        measured_on: measuredOn,
        ...(h !== null ? { height_cm: h } : {}),
        ...(w !== null ? { weight_kg: w } : {}),
      });
      return;
    }

    /**
     * 🚨 **바뀐 것만 싣는다.** `null` 은 "그날 그건 안 잰 것으로 되돌리기" 이고, 키를 아예 안
     *    보내는 것은 "그대로 두기" 다 — 둘을 합치면 안 고친 칸까지 매번 덮어쓴다.
     */
    save.mutate({
      ...(measuredOn !== log.measured_on ? { measured_on: measuredOn } : {}),
      ...(h !== log.height_cm ? { height_cm: h } : {}),
      ...(w !== log.weight_kg ? { weight_kg: w } : {}),
    });
  }

  return (
    <BottomSheet
      open={open}
      onClose={() => {
        reset();
        onClose();
      }}
      title={editing ? "잰 것 고치기" : "새로 재서 적기"}
      description={
        editing
          ? "잘못 적은 값을 바꿔요. 한쪽을 비우면 그날 그건 안 잰 것이 돼요."
          : "둘 중 하나만 적어도 괜찮아요."
      }
      footer={
        <Button block onClick={submit} disabled={save.isPending}>
          {save.isPending ? <Spinner /> : null}
          {save.isPending
            ? editing
              ? "고치는 중이에요"
              : "적는 중이에요"
            : editing
              ? "이렇게 고칠게요"
              : "적어 두기"}
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
          // 🚨 실패를 빨강으로 칠하지 않는다 (디자인 시스템 §3).
          <p role="status" className="text-body-sm text-ink-muted">
            {editing
              ? "고치지 못했어요. 그 줄은 아직 그대로니 다시 눌러 주세요."
              : "적지 못했어요. 저장된 것은 없으니 다시 눌러 주세요."}
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
