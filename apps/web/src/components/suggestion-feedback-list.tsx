"use client";

import { DomainMeta } from "@/components/domain-chip";
import { Chip, ChipRow } from "@/components/ui/chip";
import type { Suggestion, SuggestionFeedback } from "@/lib/api/types";
import { SUGGESTION_FEEDBACKS } from "@/lib/api/types";

/**
 * 07 제안 피드백 — 받은 제안이 실제로 어땠는지.
 *
 * 🚨 **교정(`Correction`)과 다른 축이다.** 교정은 기억을 고치고, 피드백은 제안 가중치만 바꾼다.
 *    응답의 `memory_changed` 가 항상 `false` 인 것이 그 사실이고, 화면 문구도 같은 말을 한다 —
 *    둘을 한 버튼 줄에 섞으면 부모가 "안 좋아했어요" 를 눌러 기억이 지워진 줄 안다.
 *
 * 🚨 **도메인 색을 쓰지 않는다.** 07 전체가 그렇다 (`DomainMeta` 주석) — 제안 목록은 네 Agent
 *    것이 다 섞여 내려올 수 있어서, 여기서 칩을 쓰면 한 화면 도메인 색 2개 상한을 깬다.
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
  pendingId,
  onSelect,
}: {
  suggestions: Suggestion[];
  /** 보내는 중인 제안. 🚨 목록 전체가 아니라 그 줄만 잠근다. */
  pendingId: string | null;
  onSelect: (suggestion: Suggestion, feedback: SuggestionFeedback) => void;
}) {
  return (
    <ul className="flex flex-col gap-3">
      {suggestions.map((suggestion) => {
        const selected = currentFeedback(suggestion.feedback);

        return (
          <li
            key={suggestion.id}
            className="rounded-card bg-surface border-line flex flex-col gap-2 border p-4"
          >
            <span className="text-caption text-ink-subtle flex items-center">
              <DomainMeta agent={suggestion.agent} />
            </span>
            <p className="text-body text-ink">{suggestion.content}</p>

            {/* 칩 줄은 가로 gap 만 준다 — 세로 여백은 칩이 터치 타깃을 채우려고 이미 갖고 있다. */}
            <ChipRow>
              {SUGGESTION_FEEDBACKS.map((feedback) => (
                <Chip
                  key={feedback}
                  selected={selected === feedback}
                  disabled={pendingId === suggestion.id}
                  onClick={() => onSelect(suggestion, feedback)}
                >
                  {FEEDBACK_LABEL[feedback]}
                </Chip>
              ))}
            </ChipRow>
          </li>
        );
      })}
    </ul>
  );
}
