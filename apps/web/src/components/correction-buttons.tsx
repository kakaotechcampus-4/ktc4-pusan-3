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
  /**
   * 바꾼 뒤에 어떻게 되는지. 없으면 줄을 세우지 않는다.
   *
   * 🚨 **판정마다 둔다. 대상(기록/기억)마다 한 줄로 묶지 않는다.** 한동안 대상마다 한 줄이라
   *    목록에 남는 판정에도 "목록에서 빠지고" 가 붙어, 바로 위 `effect` 와 반대말을 했다 (#270 리뷰).
   * 🚨 **되돌릴 수 있다고 말하지 않는다.** 교정 자체는 append-only 라 데이터가 지워지지는 않지만,
   *    화면에는 되돌리는 기능이 없다(제품 결정). "다시 고칠 수 있어요" 라고 쓰면 부모가 찾지 못할
   *    길을 약속하는 것이다. 서버도 한 번 고친 기록은 다시 받지 않는다(409 `already_corrected`).
   * 🚨 **Agent 마다 다른 것은 적지 않는다.** `stand_alone` 은 Food 는 검색하고 Activity 는 안 해서
   *    "제안의 근거로 쓰이지 않아요" 가 반만 맞다 — 확실한 "기억으로 세지 않는다" 만 적는다.
   */
  after?: string;
}

/**
 * 🚨 **여기 없는 판정은 화면에 서지 않는다.** 서버도 대상마다 받는 값이 갈린다 — 기록에
 *    `need_more_observation` · `outdated` 를 보내면 400 이다 (#277).
 *
 * 무엇이 목록에 남는지는 서버 표를 따른다. 기록은 고쳐도 목록에 남는다(`stand_alone` · `inactive`
 * 둘 다 목록에 내린다).
 *
 * 🚨 **기억 고치기는 명령이 아니라 의견이다.** 서버(#277)는 판정으로 기억의 상태를 바꾸지 않고,
 *    상태는 쌓인 기록 수로만 다시 센다 (최상위 §2 "승격은 Curator 의 반복 집계로만"). 세 판정 모두
 *    기억을 목록에 남긴다. `need_more_observation` · `outdated` 는 `strength` 만 조금 낮추고,
 *    `wrong` 은 한동안 확인됨이 되는 기준을 올려서 기록 수가 모자라면 후보로 내려간다.
 *    그래서 누르기 전에는 **어느 기억에서든 확실한 것만** 말하고, 실제로 무엇이 바뀌었는지는
 *    누른 뒤 응답으로 말한다 (`MemoryDetailSheet` 의 `CascadeResult`).
 * 🚨 기간·건수(21일 · 1건)를 쓰지 않는다 — 서버 설정값이라 바뀐다. "추천에 덜 쓰여요" 도 쓰지
 *    않는다 — 확인된 기억은 `strength` 와 상관없이 근거가 되어서 기억마다 사실 여부가 갈린다.
 */
const VERDICTS: Record<CorrectionTargetKind, VerdictSpec[]> = {
  observation: [
    {
      verdict: "once_only",
      label: "이번만 그랬어요",
      effect: "이번 한 번만 있던 일로 표시해요. 기록은 목록에 그대로 남아요.",
      after: "아이의 기억으로는 세지 않아요. 한 번 바꾸면 되돌릴 수 없어요.",
    },
    {
      verdict: "wrong",
      label: "잘못된 기록",
      effect: "잘못 들어간 기록으로 표시해요. 기록은 목록에 남아요.",
      after: "제안의 근거로도, 아이의 기억으로도 쓰이지 않아요. 한 번 바꾸면 되돌릴 수 없어요.",
    },
  ],
  affinity: [
    {
      verdict: "need_more_observation",
      label: "기록이 더 필요해요",
      effect: "아직 이르다는 의견을 남겨요. 기억은 목록에 그대로 있어요.",
      after: "기억은 기록이 쌓이는 대로 다시 정해져요.",
    },
    {
      verdict: "outdated",
      label: "지금은 달라요",
      effect: "지금은 다르다는 의견을 남겨요. 기억은 목록에 그대로 있어요.",
      after: "기억은 기록이 쌓이는 대로 다시 정해져요.",
    },
    {
      verdict: "wrong",
      label: "잘못된 기록",
      effect: "잘못된 기억이라는 의견을 남겨요. 기억은 목록에 그대로 있어요.",
      after: "한동안은 이 기억이 확인되려면 기록이 더 쌓여야 해요.",
    },
  ],
};

/**
 * 고친 기록의 상태 → 무엇으로 고쳤나, 그래서 어떻게 되나. 목록 줄과 상세 시트가 같이 쓴다.
 *
 * 🚨 **고친 기록도 목록에 남는다** (#266). 서버는 `deleted` 만 빼고 내려서, 화면이 `status` 로
 *    갈라 그리지 않으면 고친 기록과 아닌 기록이 같은 줄로 선다 — "이번만 그랬어요" 로 고친 기록이
 *    기억에 묶인 칩을 단 채 서 있으면 부모는 여전히 기억으로 세는 줄 안다.
 * 🚨 `label` 은 위 `VERDICTS.observation` 의 라벨과 같은 말이어야 한다 — 누른 버튼의 이름이
 *    그대로 줄에 남아야 무엇을 눌렀는지 알아본다. `note` 는 그 판정의 `after` 에서 "되돌릴 수
 *    없어요" 를 뺀 것이다 (이미 일어난 일이라).
 */
export const CORRECTED_OBSERVATION: Record<
  "stand_alone" | "inactive",
  { label: string; note: string }
> = {
  stand_alone: { label: "이번만 그랬어요", note: "아이의 기억으로는 세지 않아요." },
  inactive: {
    label: "잘못된 기록",
    note: "제안의 근거로도, 아이의 기억으로도 쓰이지 않아요.",
  },
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
          {asking.after ? (
            <p className="text-caption text-ink-subtle mt-2">{asking.after}</p>
          ) : null}
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
