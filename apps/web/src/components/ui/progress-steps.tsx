import { Check } from "lucide-react";

import { cn } from "@/lib/cn";

import { ICON_SIZE, ICON_STROKE } from "./icon";
import { Spinner } from "./spinner";

/**
 * 디자인 시스템 §7 진행 오버레이 (04 · SSE).
 *
 * 단계 목록을 세로로 쌓고 **현재 단계만 살린다**. 완료는 `ink-muted` + `brand` 체크,
 * 진행 중은 `ink` + 스피너, 대기는 `ink-subtle`.
 *
 * 🚨 **단계 문구는 서버가 만든다.** `step` 이벤트의 `label` 을 그대로 쓴다 —
 *    파이프라인이 바뀌면 문구도 같이 바뀌어야 하는데, 프론트에 적어 두면 어긋난다.
 *    `total` 만큼 자리를 미리 그려서 몇 칸이 남았는지 보이게 한다.
 *
 * 🚨 20초를 넘기면 화면이 이 오버레이를 걷고 부분 결과로 넘어간다 (NF-06).
 *    그 판단은 `useRunStream` 이 하고, 여기는 받은 상태만 그린다.
 */
export function ProgressSteps({
  /** 지금 몇 번째인가 (1부터). 아직 step 이 안 왔으면 0. */
  index,
  total,
  label,
  className,
}: {
  index: number;
  total: number;
  label: string;
  className?: string;
}) {
  const steps = Array.from({ length: Math.max(total, index) }, (_, i) => i + 1);

  return (
    // 단계가 바뀌는 것을 눈으로만 알 수 없다. 스크린리더에도 흘려보낸다 (문서 §7).
    <ol aria-live="polite" className={cn("flex flex-col gap-3", className)}>
      {steps.map((step) => {
        const done = step < index;
        const current = step === index;

        return (
          <li key={step} className="flex items-center gap-2.5">
            <span className="flex size-5 shrink-0 items-center justify-center">
              {done ? (
                <Check
                  aria-hidden
                  size={ICON_SIZE.md}
                  strokeWidth={ICON_STROKE}
                  className="text-brand"
                />
              ) : current ? (
                <Spinner className="text-brand size-4" />
              ) : (
                <span aria-hidden className="bg-line size-1.5 rounded-full" />
              )}
            </span>

            <span
              className={cn(
                "text-body-sm",
                done && "text-ink-muted",
                current && "text-ink",
                !done && !current && "text-ink-subtle",
              )}
            >
              {/* 지나간 단계와 남은 단계의 문구는 서버가 보내 주지 않는다. 지금 단계만 말하고
                  나머지는 자리만 지킨다 — 우리가 지어내면 실제 파이프라인과 어긋난다. */}
              {current ? label : done ? "끝났어요" : "기다리는 중"}
            </span>
          </li>
        );
      })}
    </ol>
  );
}
