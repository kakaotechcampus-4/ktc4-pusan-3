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
 */
const VERDICTS: Array<{ verdict: CorrectionVerdict; label: string }> = [
  { verdict: "confirm", label: "맞아요" },
  { verdict: "once_only", label: "한 번 본 것뿐" },
  { verdict: "outdated", label: "지금은 달라요" },
  { verdict: "wrong", label: "잘못된 기록" },
];

export function CorrectionButtons({
  onSelect,
  pending,
}: {
  onSelect: (verdict: CorrectionVerdict) => void;
  /** 보내는 중인 판정. 🚨 넷 전부가 아니라 **누른 것만** 기다린다. */
  pending: CorrectionVerdict | null;
}) {
  const busy = pending !== null;

  return (
    <div>
      <p className="text-label text-ink-muted">이 기억이 어떤가요?</p>
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
      <p className="text-caption text-ink-subtle mt-2">
        고친 기억은 다시 고칠 수 있어요. 기록이 사라지지는 않아요.
      </p>
    </div>
  );
}
