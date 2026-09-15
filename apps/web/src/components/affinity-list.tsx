"use client";

import { ChevronRight } from "lucide-react";

import { DomainMeta } from "@/components/domain-chip";
import { ICON_SIZE, ICON_STROKE } from "@/components/ui/icon";
import type { Affinity, AffinityState } from "@/lib/api/types";
import { cn } from "@/lib/cn";

/**
 * 07 프로필 목록 — **카드**다. 줄이 아니다 (`ObservationList` 주석 참고).
 *
 * 🚨 **승격 상태를 색으로 구분하지 않는다.** 이 시스템의 색은 뜻이 1:1 로 정해져 있고
 *    (문서 §3) "확정됨" 은 그 목록에 없다. 대신 **카드 실루엣**이 상태를 진다 —
 *    `confirmed` 는 `line` 실선, `candidate` 는 `line-strong` 점선(05 의 `card-general` 과
 *    같은 언어로 "아직 단단하지 않음"), `is_stale` 은 `surface-muted`(근거에서 빠져 있다는
 *    사실 그대로 · 문서 §7 "접힌 영역"). 그리고 모양만으로 두지 않고 **상태를 글자로도 쓴다.**
 *
 * 🚨 **`state` 와 `state_reason` 을 프론트가 만들지 않는다.** 승격은 Curator 의 반복 집계로만
 *    일어나고(CLAUDE.md §2), "서로 다른 3일에 관찰됐어요" 는 서버가 임계값을 알고 쓴 문장이다.
 *
 * 🚨 **`is_stale` 인 프로필을 단독 근거처럼 보이게 두지 않는다** (NF-08). 6개월이 지났다는
 *    날짜 사실은 `state_reason` 이 말하고, 그래서 어떻게 되는지는 고정 문구가 말한다.
 */
const STATE_LABEL: Record<AffinityState, string> = {
  candidate: "후보",
  confirmed: "확인됨",
  archived: "보관됨",
};

/** 좋아함/싫어함은 별도 배열이 아니라 `polarity` 로 내려온다 (계약서 §08). */
function polarityLabel(polarity: Affinity["polarity"]): string {
  if (polarity === 1) return "좋아해요";
  if (polarity === -1) return "안 좋아해요";
  if (polarity === 0) return "반응이 갈려요";
  // confirmed 면 NOT NULL 이다. candidate 에서만 비어 있을 수 있다.
  return "아직 판단하지 않았어요";
}

export function AffinityList({
  affinities,
  onOpen,
}: {
  affinities: Affinity[];
  onOpen: (affinity: Affinity) => void;
}) {
  return (
    <ul className="flex flex-col gap-3">
      {affinities.map((affinity) => (
        <li key={affinity.id}>
          <AffinityCard affinity={affinity} onOpen={() => onOpen(affinity)} />
        </li>
      ))}
    </ul>
  );
}

function AffinityCard({ affinity, onOpen }: { affinity: Affinity; onOpen: () => void }) {
  const stale = affinity.is_stale;
  const candidate = affinity.state === "candidate";

  return (
    <button
      type="button"
      onClick={onOpen}
      className={cn(
        "rounded-card ease-standard flex w-full items-start gap-2 border p-4 text-left transition-colors duration-120",
        // 🚨 세 모양이 서로 배타적이다. `stale` 이 먼저다 — 후보인데 오래된 것은
        //    "아직 확정 안 됨" 보다 "근거에서 빠져 있음" 이 부모에게 더 중요한 사실이다.
        stale
          ? "bg-surface-muted active:bg-line border-transparent"
          : candidate
            ? "bg-canvas border-line-strong active:bg-surface-muted border-dashed"
            : "bg-surface border-line active:bg-surface-muted",
      )}
    >
      <span className="flex min-w-0 flex-1 flex-col gap-1">
        <span
          className={cn(
            "text-caption flex flex-wrap items-center gap-1",
            stale ? "text-ink-muted" : "text-ink-subtle",
          )}
        >
          <DomainMeta agent={affinity.domain} />
          <span>· {STATE_LABEL[affinity.state]}</span>
        </span>

        <span className={cn("text-title", stale ? "text-ink-muted" : "text-ink")}>
          {affinity.merge_key}
        </span>

        <span className="text-label text-ink-muted">
          {polarityLabel(affinity.polarity)} · 기록 {affinity.observation_count}건
        </span>

        {/* 서버가 임계값을 알고 쓴 문장이다. 화면이 바꿔 쓰지 않는다. */}
        <span className="text-body-sm text-ink-muted">{affinity.state_reason}</span>

        {stale ? (
          <span className="text-caption text-ink-muted">
            6개월이 지나서 이 프로필만으로는 추천을 만들지 않아요.
          </span>
        ) : null}
      </span>

      <ChevronRight
        aria-hidden
        size={ICON_SIZE.md}
        strokeWidth={ICON_STROKE}
        className={cn("mt-0.5 shrink-0", stale ? "text-ink-muted" : "text-ink-subtle")}
      />
    </button>
  );
}
