"use client";

import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
import type { CorrectionVerdict } from "@/lib/api/types";

/**
 * 4버튼 Correction (CLAUDE.md §5 · 디자인 시스템 §11).
 *
 * 🚨 **넷 다 `btn-secondary` 다. `wrong` 도 빨강이 아니다.** 교정은 사고가 아니라 부모가
 *    자기 기억을 고치는 일이다. `danger` 는 알레르기·건강 중단·파괴적 확정 전용이고
 *    (문서 §3), 교정은 그 셋 어디에도 없다.
 *
 * 🚨 **여기에 확인 단계를 두지 않는다.** 승인 게이트는 딱 2곳이고 늘리지도 줄이지도 않는다
 *    (CLAUDE.md §2). 프로토타입은 `wrong` 앞에 "지우면 되돌릴 수 없어요" 경고 화면을 뒀는데,
 *    그건 세 번째 게이트다. 게다가 사실도 아니다 — 교정은 append-only 라 반대 교정으로
 *    되돌린다 (`outdated` 뒤에 `confirm` · 계약서 §08). 그 사실을 화면이 말한다.
 *
 * 🚨 **무엇이 다시 계산됐는지는 서버만 안다.** 응답의 `cascade` 를 화면이 그대로 옮기고,
 *    프론트가 "추천이 바뀔 거예요" 를 추측하지 않는다.
 *
 * 🚨 **관찰과 프로필을 "기억" 한 단어로 묶지 않는다** (`targetKind`). 07 의 논지 전체가
 *    "관찰 1건과 confirmed 프로필이 같은 무게로 읽히면 최상위 §2 가 화면에서 사라진다" 인데,
 *    목록은 줄과 카드로 갈라 놓고 교정하는 순간 문구가 둘을 도로 합치면 부모는 프로필을
 *    고치면서 자기가 관찰 한 건을 고치는 줄 안다.
 */
const VERDICTS: Array<{ verdict: CorrectionVerdict; label: string }> = [
  { verdict: "confirm", label: "맞아요" },
  { verdict: "once_only", label: "한 번 본 것뿐" },
  { verdict: "outdated", label: "지금은 달라요" },
  { verdict: "wrong", label: "잘못된 기록" },
];

export function CorrectionButtons({
  targetKind,
  onSelect,
  pending,
}: {
  /** 무엇을 고치는 중인가. 문구가 갈린다 — 관찰 1건과 프로필은 다른 것이다. */
  targetKind: "observation" | "affinity";
  onSelect: (verdict: CorrectionVerdict) => void;
  /** 보내는 중인 판정. 🚨 넷 전부가 아니라 **누른 것만** 기다린다. */
  pending: CorrectionVerdict | null;
}) {
  const busy = pending !== null;

  return (
    <div>
      <p className="text-label text-ink-muted">
        {targetKind === "observation" ? "이 기록이 어떤가요?" : "이 기억이 맞나요?"}
      </p>
      {/* 넷이 같은 무게다. 순서는 교정의 세기 순(맞음 → 한 번 → 지났음 → 틀림)이고,
          한 줄에 둘씩 둬서 좁은 폰에서도 문구가 잘리지 않는다. */}
      <div className="mt-2 grid grid-cols-2 gap-2">
        {VERDICTS.map(({ verdict, label }) => (
          <Button
            key={verdict}
            variant="secondary"
            size="compact"
            disabled={busy}
            onClick={() => onSelect(verdict)}
          >
            {pending === verdict ? <Spinner /> : null}
            {label}
          </Button>
        ))}
      </div>
      {/* 🚨 **여기서 되돌릴 수 있다는 말만 한다.** 교정은 append-only 라 반대 교정으로
          되돌리는데(위 🚨), 그 경로가 이 창 말고는 없다 — 관찰 탭의 "고쳐서 뺀 기억" 필터로
          다시 열 수 있게 해 두고 그 사실까지 같이 말한다. 둘 중 하나라도 없으면 부모에게
          `잘못된 기록` 은 되돌릴 수 없는 동작이 되고, 그러면 확인 단계를 뺀 근거가 무너진다. */}
      <p className="text-caption text-ink-subtle mt-2">
        이 창에서 바로 다시 고칠 수 있어요. 창을 닫은 뒤에는 목록의 &quot;고쳐서 뺀 기록&quot; 에서
        찾을 수 있어요.
      </p>
    </div>
  );
}
