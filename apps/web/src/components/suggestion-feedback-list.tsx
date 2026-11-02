"use client";

import { domainField, domainLabel } from "@/components/domain-chip";
import { Chip, ChipRow } from "@/components/ui/chip";
import { DOMAIN_ICON } from "@/components/ui/icon";
import { IconTile } from "@/components/ui/icon-tile";
import { SkeletonText, SkeletonTile } from "@/components/ui/skeleton";
import type { ReceivedSuggestion, SuggestionFeedback } from "@/lib/api/types";
import { SUGGESTION_FEEDBACKS } from "@/lib/api/types";
import { cn } from "@/lib/cn";

/**
 * 07 제안 피드백 — 받은 제안이 실제로 어땠는지.
 *
 * 🚨 **교정(`Correction`)과 다른 축이다.** 교정은 기억을 고치고, 피드백은 제안 가중치만 바꾼다.
 *    응답의 `memory_changed` 가 항상 `false` 인 것이 그 사실이고, 화면 문구도 같은 말을 한다 —
 *    둘을 한 버튼 줄에 섞으면 부모가 "안 좋아했어요" 를 눌러 기억이 지워진 줄 안다.
 *
 * 🚨 **기록 · 기억 탭과 같은 뼈대다** (#592). 왼쪽 타일(어느 영역인지) 옆에 제목 · 메타 · 칩이 선다 —
 *    한 화면의 세 탭이 왼쪽 기준선과 글자 층이 다르면 같은 아이에 대한 목록이 아니라 남남으로 읽힌다.
 *    - 타일은 **그 영역의 도메인 색**이다. 07 의 도메인 색은 타일 하나까지라는 예외(문서 §3 · 디자인
 *      시스템 §3 예외 ㉡)가 이 탭에도 그대로 걸린다. 🚨 **색 면을 타일 밖으로 넓히지 않는다** —
 *      칩 · 줄 바탕은 도메인 색이 아니다. 영역 이름은 메타 줄에 글자로 함께 선다
 *    - 제목은 제안 문장이고 `body` 600 이다 (기록 줄 제목과 같은 층)
 *    - 메타는 가운뎃점 없이 간격으로 가른다 (#524)
 *    - 모양은 **카드**(`card` 14 · 간격 12)다. 줄 전체가 누르는 버튼인 기록 · 기억과 달리 안에 고르는
 *      칩이 서서, 줄을 버튼으로 만들 수 없다 — 그래서 끝의 `>` 도 없다
 *
 * 🚨 **채택한 일반 추천도 여기서 평가한다** (#509). 줄에 "일반 추천" 을 **글자로** 적는다 —
 *    우리 아이 기준 제안인 척하지 않는 것이 이 라벨의 일이다 (CLAUDE.md §2). 색 · 점선으로 가르지 않는다.
 * ⚠️ 05 처럼 컴포넌트를 나누지 않는다. 05 에서 나눈 이유는 **근거를 그리는 자리**라서다 — 한 컴포넌트에
 *    플래그를 넣으면 근거 0건이 개인화로 그려질 길이 생긴다. 이 목록은 근거를 그리지 않고, 일반 추천
 *    타입(`ReceivedGeneralSuggestion`)에는 근거 필드가 아예 없다.
 */
const FEEDBACK_LABEL: Record<SuggestionFeedback, string> = {
  child_liked: "아이가 좋아했어요",
  child_disliked: "아이가 안 좋아했어요",
  not_acted: "안 해봤어요",
};

/** `Suggestion.feedback` 은 계약서에서 `unknown` 이다. 아는 값일 때만 켜진 칩으로 그린다. */
function currentFeedback(value: unknown): SuggestionFeedback | null {
  return SUGGESTION_FEEDBACKS.includes(value as SuggestionFeedback)
    ? (value as SuggestionFeedback)
    : null;
}

export function SuggestionFeedbackList({
  suggestions,
  pendingIds,
  onSelect,
}: {
  suggestions: ReceivedSuggestion[];
  /** 보내는 중인 제안들. 🚨 목록 전체가 아니라 그 줄만 잠근다. */
  pendingIds: ReadonlySet<string>;
  onSelect: (suggestion: ReceivedSuggestion, feedback: SuggestionFeedback) => void;
}) {
  return (
    <ul className="flex flex-col gap-3">
      {suggestions.map((suggestion) => {
        const selected = currentFeedback(suggestion.feedback);

        return (
          <li
            key={suggestion.id}
            className="rounded-card bg-surface border-line flex items-start gap-3 border p-4"
          >
            <IconTile
              icon={DOMAIN_ICON[suggestion.agent]}
              tone="plain"
              className={domainField(suggestion.agent)}
            />

            <div className="flex min-w-0 flex-1 flex-col gap-1.5">
              <p className="text-body text-ink font-semibold">{suggestion.content}</p>
              <span className="text-caption text-ink-subtle flex flex-wrap gap-x-2">
                <span>{domainLabel(suggestion.agent)}</span>
                {suggestion.kind === "general" ? <span>일반 추천</span> : null}
              </span>

              {/* 칩 줄은 가로 gap 만 준다 — 세로 여백은 칩이 터치 타깃을 채우려고 이미 갖고 있다. */}
              <ChipRow>
                {SUGGESTION_FEEDBACKS.map((feedback) => (
                  <Chip
                    key={feedback}
                    selected={selected === feedback}
                    disabled={pendingIds.has(suggestion.id)}
                    onClick={() => onSelect(suggestion, feedback)}
                  >
                    {FEEDBACK_LABEL[feedback]}
                  </Chip>
                ))}
              </ChipRow>
            </div>
          </li>
        );
      })}
    </ul>
  );
}

/**
 * 받는 동안의 목록. 진짜 줄과 **같은 상자 · 같은 줄 높이**다 (CLS · `components/ui/skeleton.tsx`) —
 * 타일 · 제안 문장 · 영역 · 피드백 칩 셋. 칩은 진짜 칩처럼 누르는 줄 높이(`min-h-touch`)를 갖고 같은 폭에서 접힌다.
 * 🚨 진짜 줄의 여백을 바꾸면 여기도 같이 바꾼다. 건수 줄은 화면 쪽이 잡는다.
 */
export function SuggestionFeedbackListSkeleton({ rows = 2 }: { rows?: number }) {
  return (
    <ul aria-hidden className="flex flex-col gap-3">
      {Array.from({ length: rows }, (_, i) => (
        <li
          key={i}
          className="rounded-card bg-surface border-line flex items-start gap-3 border p-4"
        >
          <SkeletonTile />
          <div className="flex min-w-0 flex-1 flex-col gap-1.5">
            <SkeletonText width="3/4" />
            <SkeletonText size="caption" width="1/4" />
            <ChipRow>
              {["w-28", "w-32", "w-20"].map((width) => (
                <span key={width} className="min-h-touch flex items-center py-2">
                  <span className={cn("bg-line animate-skeleton min-h-chip rounded-full", width)} />
                </span>
              ))}
            </ChipRow>
          </div>
        </li>
      ))}
    </ul>
  );
}
