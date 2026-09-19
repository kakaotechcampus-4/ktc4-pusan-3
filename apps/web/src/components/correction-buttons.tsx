"use client";

import { josa } from "es-hangul";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
import type { CorrectionVerdict } from "@/lib/api/types";

/**
 * 교정 (CLAUDE.md §5 Correction · 디자인 시스템 §11).
 *
 * 🚨 **기록과 기억은 서로 다른 것을 묻는다.** 기록은 "그 일이 실제로 어땠나" 한 건에 대한 말이고,
 *    기억은 "그래서 아이가 그렇다고 볼 수 있나" 에 대한 말이다. 같은 버튼 묶음을 양쪽에 쓰면
 *    기억에 "이번만 그랬어요" 가 서고 기록에 "더 지켜봐요" 가 서는데, 둘 다 뜻이 안 맞는다.
 *
 * 🚨 **묻는 버튼은 전부 `btn-secondary` 다. `wrong` 도 빨강이 아니다.** 교정은 사고가 아니라 부모가
 *    자기 기억을 고치는 일이다. `danger` 는 알레르기·건강 중단·파괴적 확정 전용이고
 *    (문서 §3), 교정은 그 셋 어디에도 없다.
 *
 * 🚨 **확인 단계는 승인 게이트가 아니다.** 승인 게이트는 딱 2곳이고 늘리지 않는다
 *    (CLAUDE.md §2) — 그래서 여기에는 `btn-approve` 도 `caution` 도 쓰지 않는다.
 *    이건 "무엇이 바뀌는지 보여주고 한 번 더 묻는" 단계다. 게이트가 되돌릴 수 없는 것을 막는
 *    장치라면, 이건 **되돌릴 수 있다는 사실까지 같이 말해 주는** 자리다.
 *    🚨 그래서 확인 문구는 **일어날 일만** 적는다 — "추천이 바뀔 거예요" 같은 예측을 쓰지 않는다
 *    (무엇이 다시 계산됐는지는 응답의 `cascade` 만 안다).
 *
 * 🚨 **무엇이 다시 계산됐는지는 서버만 안다.** 응답의 `cascade` 를 화면이 그대로 옮긴다.
 */
export type CorrectionTargetKind = "observation" | "affinity";

interface VerdictSpec {
  verdict: CorrectionVerdict;
  label: string;
  /** 누르면 실제로 무엇이 바뀌는지. 🚨 확실한 것만 적는다. */
  effect: string;
}

/**
 * 🚨 **여기 없는 판정은 화면에 서지 않는다.** `CorrectionVerdict` 타입에는 `confirm` 도 있지만
 *    그건 지난 이력에 실려 오는 값이라 남겨 둔 것이고, 묻는 자리에는 넣지 않는다.
 */
const VERDICTS: Record<CorrectionTargetKind, VerdictSpec[]> = {
  observation: [
    {
      verdict: "once_only",
      label: "이번만 그랬어요",
      effect: "이번 한 번만 있던 일로 표시해요. 기록은 목록에 그대로 남아요.",
    },
    {
      verdict: "wrong",
      label: "잘못된 기록",
      effect: "이 기록을 목록에서 빼요.",
    },
  ],
  affinity: [
    {
      verdict: "need_more_observation",
      label: "기록이 더 필요해요",
      effect: "아직 확정하지 않고 더 지켜봐요. 쌓인 기록은 그대로 둬요.",
    },
    {
      verdict: "outdated",
      label: "지금은 달라요",
      effect: "지금은 다르다고 표시하고 이 기억을 목록에서 빼요.",
    },
    {
      verdict: "wrong",
      label: "잘못된 기록",
      effect: "이 기억을 목록에서 빼요. 쌓인 기록은 그대로 남아요.",
    },
  ],
};

/**
 * 🚨 **되돌릴 수 있다고 말하지 않는다.** 교정 자체는 append-only 라 데이터가 지워지지는 않지만,
 *    화면에는 되돌리는 기능이 없다(제품 결정). "다시 고칠 수 있어요" 라고 쓰면 부모가 찾지 못할
 *    길을 약속하는 것이다 — 목록에서 빠진다는 사실과, 앞으로 어떻게 되는지만 말한다.
 */
const RECOVERY: Record<CorrectionTargetKind, string> = {
  observation: "목록에서 빠지고 되돌릴 수 없어요. 제안의 근거로도 쓰이지 않아요.",
  affinity: "목록에서 빠지고 되돌릴 수 없어요. 같은 것이 다시 쌓이면 새로 만들어져요.",
};

export function CorrectionButtons({
  targetKind,
  onSelect,
  pending,
}: {
  /** 무엇을 고치는 중인가. 묻는 것도 문구도 갈린다 — 기록 한 건과 기억은 다른 것이다. */
  targetKind: CorrectionTargetKind;
  onSelect: (verdict: CorrectionVerdict) => void;
  /** 보내는 중인 판정. 🚨 전부가 아니라 **누른 것만** 기다린다. */
  pending: CorrectionVerdict | null;
}) {
  const [asking, setAsking] = useState<VerdictSpec | null>(null);
  const busy = pending !== null;

  if (asking) {
    return (
      <div className="border-line rounded-card bg-surface-muted border p-4">
        {/* 🚨 `role="status"` 로 알린다 — 버튼 묶음이 통째로 바뀌는데 눈으로만 알 수 있으면
            스크린리더 사용자는 자기가 무엇을 확인하는 중인지 모른다. */}
        <div role="status">
          {/* 🚨 라벨에 조사를 박지 않는다. 라벨은 데이터고 조사는 문법이라, 박아 두면 다른
              문장에서 못 쓴다 — 받침으로 고르는 것은 `josa` 가 한다 (키가 `"으로/로"` 순서다). */}
          <p className="text-body text-ink">{josa(asking.label, "으로/로")} 바꿀까요?</p>
          <p className="text-body-sm text-ink-muted mt-1">{asking.effect}</p>
          <p className="text-caption text-ink-subtle mt-2">{RECOVERY[targetKind]}</p>
        </div>

        {/* 🚨 `btn-approve` 도 `caution` 도 쓰지 않는다 — 그 둘은 승인 게이트 2곳 전용이다. */}
        <div className="mt-3 flex gap-2">
          <Button
            variant="primary"
            size="compact"
            className="flex-1"
            disabled={busy}
            onClick={() => onSelect(asking.verdict)}
          >
            {busy ? <Spinner /> : null}
            바꾸기
          </Button>
          <Button variant="tertiary" size="compact" disabled={busy} onClick={() => setAsking(null)}>
            그만두기
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div>
      <p className="text-label text-ink-muted">
        {targetKind === "observation" ? "이 기록이 어떤가요?" : "이 기억이 맞나요?"}
      </p>
      {/* 순서는 교정의 세기 순이고, 한 줄에 둘씩 둬서 좁은 폰에서도 문구가 잘리지 않는다. */}
      <div className="mt-2 grid grid-cols-2 gap-2">
        {VERDICTS[targetKind].map((spec) => (
          <Button
            key={spec.verdict}
            variant="secondary"
            size="compact"
            onClick={() => setAsking(spec)}
          >
            {spec.label}
          </Button>
        ))}
      </div>
      <p className="text-caption text-ink-subtle mt-2">고르면 무엇이 바뀌는지 먼저 보여드려요.</p>
    </div>
  );
}
