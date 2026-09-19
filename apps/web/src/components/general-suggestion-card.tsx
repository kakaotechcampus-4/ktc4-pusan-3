import { DomainChip } from "@/components/domain-chip";
import { CountChip } from "@/components/ui/chip";
import type { GeneralSuggestion } from "@/lib/api/types";

/**
 * 디자인 시스템 §7 `card-general` — 또래 기준 일반 추천.
 *
 * 근거 Memory 가 부족할 때 개인화 **대신** 나간다 (CLAUDE.md §2). 05 화면에서 개인화 목록
 * (`SuggestionList`)이 서는 자리에 이 카드가 선다 — 둘이 같이 나오는 경우는 없다.
 *
 * 🚨 **개인화 줄과 같은 컴포넌트로 만들지 않는다.** 한 컴포넌트에 플래그를 넣으면 언젠가
 *    근거 0건인데 개인화로 그려진다 (문서 §7). 타입도 따로라(`GeneralSuggestion` 에는
 *    `evidence` 필드가 아예 없다) 이 카드에 개인화 제안을 넘기면 컴파일이 막는다.
 *
 * 🚨 **"또래 기준" 이라는 사실을 카드가 직접 말한다.** 라벨과 기록 건수는 장식이 아니라
 *    이 카드의 본체다 — 근거가 없는데 "우리 아이 맞춤" 인 척하지 않는 것이 이 화면의 전부다.
 *    그래서 둘 다 빼거나 접을 수 없게 붙박이로 둔다.
 * 🚨 **색으로 구분하지 않는다.** 개인화와 일반을 가르는 것은 라벨과 건수지 색이 아니다
 *    (문서 §3). 도메인 칩의 색은 여기서도 "어느 Agent 에서 왔나" 하나만 뜻한다.
 *
 * 🚨 **고르는 버튼이 없다.** 승인 게이트 ㉠ 은 되돌릴 수 없는 캘린더 쓰기고, 이 추천은
 *    이 아이의 기록에서 나온 것이 아니다. 여기서 할 일은 고르는 것이 아니라 바로 아래
 *    질문 1개에 답해서 다음 번 추천을 우리 아이 것으로 만드는 것이다.
 *
 * ⚠️ `basis` 는 **서버 문구**다. 무엇을 기준으로 골랐는지는 만든 쪽만 안다 —
 *    프론트가 "또래 기준" 뒤의 설명을 지어내지 않는다.
 */
export function GeneralSuggestionCard({
  suggestion,
  recordCount,
}: {
  suggestion: GeneralSuggestion;
  /** 이 주제로 쌓인 기록 건수. `Scarcity.count` 를 그대로 받는다 — 숨기지 않는다. */
  recordCount: number;
}) {
  return (
    // 🚨 점선 테두리가 개인화 줄(실선 `line`)과 다른 신호다. 바탕은 `canvas` 라
    //    카드가 면으로 떠오르지 않는다 — 확정된 것이 아니라는 것을 형태가 먼저 말한다.
    <li className="border-line-strong rounded-card bg-canvas border border-dashed p-4">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1.5">
        <DomainChip agent={suggestion.agent} />
        <span className="text-label text-ink-muted">또래 기준 일반 추천</span>
        <CountChip>기록 {recordCount}건</CountChip>
      </div>

      <p className="text-body text-ink mt-3">{suggestion.content}</p>
      <p className="text-body-sm text-ink-muted mt-1">{suggestion.basis}</p>
    </li>
  );
}
