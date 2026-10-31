import { DomainChip } from "@/components/domain-chip";
import { Checkbox } from "@/components/ui/checkbox";
import { CountChip } from "@/components/ui/chip";
import type { GeneralSuggestion } from "@/lib/api/types";
import { cn } from "@/lib/cn";

/**
 * 디자인 시스템 §7 `card-general` — 또래 기준 일반 추천.
 *
 * 근거 Memory 가 부족한 후보는 개인화 **대신** 이 카드로 나간다 (CLAUDE.md §2).
 * ⚠️ 같은 Agent 안에서도 개인화 목록(`SuggestionList`)과 **같이 설 수 있다** — Agent 가 후보마다 아이 기록
 *    근거로 `kind` 를 정한다 (#512 리뷰 · 서버 `build()`).
 *
 * 🚨 **개인화 줄과 같은 컴포넌트로 만들지 않는다.** 한 컴포넌트에 플래그를 넣으면 언젠가
 *    근거 0건인데 개인화로 그려진다 (문서 §7). 타입도 따로라(`GeneralSuggestion.evidence` 는
 *    아이 기록이 아니라 문서 행 `DocEvidence[]` 다) 이 카드에 개인화 제안을 넘기면 컴파일이 막는다.
 *
 * 🚨 **"또래 기준" 이라는 사실을 카드가 직접 말한다.** 라벨과 기록 건수는 장식이 아니라
 *    이 카드의 본체다 — 근거가 없는데 "우리 아이 맞춤" 인 척하지 않는 것이 이 화면의 전부다.
 *    그래서 둘 다 빼거나 접을 수 없게 붙박이로 둔다.
 * 🚨 **색으로 구분하지 않는다.** 개인화와 일반을 가르는 것은 라벨과 건수지 색이 아니다
 *    (문서 §3). 도메인 칩의 색은 여기서도 "어느 Agent 에서 왔나" 하나만 뜻한다.
 *
 * 🚨 **개인화 줄처럼 고를 수 있다** (#509). 한동안 고르는 칸이 없었다 — "이 추천은 이 아이의
 *    기록에서 나온 것이 아니다" 가 이유였는데, 그러면 일반 추천만 나온 화면에서 보호자가 할 수
 *    있는 일이 없었다. 캘린더에 쓰기 직전에는 승인 게이트 ㉠(승인 시트)이 그대로 서므로, 고르는
 *    것 자체를 막을 이유가 되지 못한다. 고르는 수단도 개인화 줄과 같다 — 제안 문장이 체크박스의
 *    라벨이고 다음 행동은 목록 아래 한 곳이다 (`suggestion-list.tsx` 머리말).
 * 🚨 **health 는 고르는 칸이 없다** — 개인화 줄과 같은 이유다 (진단하지 않는다 · CLAUDE.md §2).
 *
 * ⚠️ `basis` 는 **서버 문구**다. 무엇을 기준으로 골랐는지는 만든 쪽만 안다 —
 *    프론트가 "또래 기준" 뒤의 설명을 지어내지 않는다.
 */
export function GeneralSuggestionCard({
  suggestion,
  recordCount,
  selected,
  onToggleSelect,
  busy,
}: {
  suggestion: GeneralSuggestion;
  /** 이 주제로 쌓인 기록 건수. `Scarcity.count` 를 그대로 받는다 — 숨기지 않는다. */
  recordCount: number;
  selected: boolean;
  onToggleSelect: () => void;
  /** 채택 중이거나 채택이 끝났다. 고르기를 잠근다 — 보내는 값이 바뀌면 안 된다. */
  busy: boolean;
}) {
  const isHealth = suggestion.agent === "health";
  const placeNotice = suggestion.place_notice?.trim();

  return (
    // 🚨 점선 테두리가 개인화 줄(실선 `line`)과 다른 신호다. 바탕은 `canvas` 라
    //    카드가 면으로 떠오르지 않는다 — 확정된 것이 아니라는 것을 형태가 먼저 말한다.
    // 🚨 고르면 **테두리 색만** `brand` 가 된다 (개인화 줄과 같은 "골랐음"). 점선은 남긴다 —
    //    고른다고 우리 아이 기준 추천이 되는 것이 아니다. 색만으로 말하지 않는다 — 체크 표식이 함께 선다.
    <li
      className={cn(
        "rounded-card bg-canvas ease-standard border border-dashed p-4 transition-colors duration-120",
        selected ? "border-brand" : "border-line-strong",
      )}
    >
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1.5">
        <DomainChip agent={suggestion.agent} />
        <span className="text-label text-ink-muted">또래 기준 일반 추천</span>
        <CountChip>기록 {recordCount}건</CountChip>
      </div>

      {isHealth ? (
        <p className="text-body text-ink mt-3">{suggestion.content}</p>
      ) : (
        <Checkbox
          checked={selected}
          onChange={() => !busy && onToggleSelect()}
          label={suggestion.content}
          className="mt-3 py-0"
        />
      )}

      {/* 체크박스 칸(20)과 사이(12)만큼 들여써서 기준 문구 · 출처가 제안 문장과 한 세로선에 선다. */}
      <div className={isHealth ? "" : "pl-8"}>
        {/* 장소 확인 문구는 제안 문장에 딸린 말이라 바로 아래, 기준 문구보다 위다 (#516).
            기준 문구는 아래 출처와 한 묶음이다. 개인화 줄과 같은 글씨다. */}
        {placeNotice ? <p className="text-caption text-ink-muted mt-1">{placeNotice}</p> : null}
        <p className="text-body-sm text-ink-muted mt-1">{suggestion.basis}</p>

        {/* 🚨 일반 추천의 근거는 **문서 행**이다 (최상위 §2). 출처를 같이 적는다 — CC BY 문서는 출처
          표시가 이용 조건이고, 무엇을 보고 골랐는지가 "또래 기준" 의 근거다. "사용한 기록 N건" 으로
          세지 않는다 (아이 기록이 아니다). */}
        {suggestion.evidence.length > 0 ? (
          <ul className="mt-3 flex flex-col gap-1">
            {suggestion.evidence.map((doc) => (
              <li key={doc.ref.id} className="text-caption text-ink-subtle">
                참고 · {doc.title} ({doc.source_org}, {doc.source_title})
              </li>
            ))}
          </ul>
        ) : null}
      </div>
    </li>
  );
}
