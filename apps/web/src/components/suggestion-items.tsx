"use client";

import { ChevronDown, ChevronUp } from "lucide-react";
import { useId, useState } from "react";

import { ICON_SIZE, ICON_STROKE } from "@/components/ui/icon";
import type { Agent } from "@/lib/api/types";

/** 접힌 채로 보이는 개수. 넘으면 "N개 더 보기" 가 선다. */
const VISIBLE = 4;

/**
 * 추천을 해 보는 데 필요한 것 — 필요한 재료 · 준비물 (#659). 개인화 줄(`SuggestionList`)과 일반 추천 카드
 * (`GeneralSuggestionCard`)가 **이 부분만** 같이 쓴다. 두 카드를 합치지 않는다 (CLAUDE.md §4 — 일반과 개인화는
 * 한 컴포넌트로 그리지 않는다).
 *
 * 고르기 전에 무엇이 필요한지 보여 주는 자리다. 한동안 이 정보는 채택한 뒤 일정 초안에서야 보였다 —
 * 서버가 초안의 준비물을 만드는 원본(`suggestion.items`)과 같은 것이다.
 *
 * 🚨 **읽기 전용이다.** 항목마다 체크박스를 두지 않는다. 이 카드에는 추천을 고르는 체크박스가 이미 있어서
 *    하나 더 두면 무엇을 고르는지 섞이고, "준비했어요" 는 일정이 생긴 뒤 캘린더가 받는다 (`EventItem.is_prepared`).
 * 🚨 **누를 수 없는 칩이다** — 모양은 디자인 시스템 §7 `chip-evidence` 의 값(`surface` · `line` 1px · `ink-muted` ·
 *    caption · full)을 그대로 쓴다. 새 값을 만들지 않는다. 근거 칩 컴포넌트(`EvidenceChip`)를 부르지 않는 것은
 *    이것이 근거가 아니어서다 — 이름이 뜻을 나른다.
 * 🚨 **접는 것은 항목 목록뿐이다.** 근거는 접지 않는다(`SuggestionList` 머리말)는 규칙과 부딪히지 않는다 —
 *    재료가 열 개 넘게 오면 카드 셋을 견주는 화면에서 재료가 제안 문장보다 길어진다.
 * 🚨 **펼치기는 고르기와 따로다.** 버튼은 체크박스의 `<label>` 밖에 서서, 눌러도 추천 선택이 바뀌지 않는다.
 *
 * 비어 있으면(또는 실서버가 아직 칸을 안 내리면) 아무것도 그리지 않는다 — 라벨만 선 빈 영역은 "필요한 게 없다"
 * 인지 "못 받았다" 인지 가를 수 없다. health 는 무엇을 하라고 권하지 않아서 영역 자체가 없다.
 */
export function SuggestionItems({
  agent,
  items,
  className,
  content,
}: {
  agent: Agent;
  /** 서버 순서 그대로. ⚠️ 실서버가 칸을 내리기 전에는 `undefined` 로 온다 (`types.ts` 의 `Suggestion.items`). */
  items: readonly string[] | undefined;
  className?: string;
  /**
   * 그 추천의 제안 문장. 이것과 같은 항목은 그리지 않는다 (`itemNames` · #892).
   * 필수로 둔 것은 카드가 넘기는 것을 빠뜨리면 도서 이름이 칩으로 한 번 더 서기 때문이다.
   */
  content: string;
}) {
  const [expanded, setExpanded] = useState(false);
  const id = useId();

  const term = ITEM_TERM[agent];
  const names = itemNames(agent, items, content);
  if (term === null || names.length === 0) return null;

  const hidden = names.length - VISIBLE;
  const shown = expanded || hidden <= 0 ? names : names.slice(0, VISIBLE);
  const labelId = `${id}-label`;
  const listId = `${id}-list`;

  return (
    <div className={className}>
      <p id={labelId} className="text-caption text-ink-subtle">
        {term}
      </p>
      {/* 🚨 넘치면 줄을 바꾼다 — 가로 스크롤하지 않는다 (문서 §7 칩 줄). 긴 이름은 칩 안에서 줄을 바꾼다.
          🚨 `break-words` 가 아니라 `wrap-anywhere` 다. 전역이 `keep-all` 이라 띄어쓰기 없는 이름은 끊을 자리가 없는데,
          `break-word` 는 칩의 최소 폭을 줄이지 않아서 글자가 칩 테두리 밖으로 나가고 카드 끝에서 잘렸다.
          띄어쓰기가 있으면 여전히 어절에서 먼저 줄을 바꾼다. */}
      <ul
        id={listId}
        aria-labelledby={labelId}
        className="mt-1.5 flex flex-wrap gap-x-1.5 gap-y-1.5"
      >
        {shown.map((name, i) => (
          // 같은 이름이 두 번 올 수 있다 (서버는 초안에서만 합친다) — 자리로 키를 만든다.
          <li
            key={`${i}:${name}`}
            className="text-caption min-h-chip border-line bg-surface text-ink-muted inline-flex max-w-full items-center rounded-full border px-2.5 py-0.5 wrap-anywhere"
          >
            {name}
          </li>
        ))}
      </ul>
      {hidden > 0 ? (
        <button
          type="button"
          onClick={() => setExpanded((open) => !open)}
          aria-expanded={expanded}
          aria-controls={listId}
          className="min-h-touch text-label text-ink-muted inline-flex items-center gap-1"
        >
          {expanded ? "접기" : `${hidden}개 더 보기`}
          {/* 🚨 방향은 아이콘을 갈아 끼워 말한다 — 회전시키지 않는다 (`Select` 와 같다). */}
          {expanded ? (
            <ChevronUp aria-hidden size={ICON_SIZE.sm} strokeWidth={ICON_STROKE} />
          ) : (
            <ChevronDown aria-hidden size={ICON_SIZE.sm} strokeWidth={ICON_STROKE} />
          )}
        </button>
      ) : null}
    </div>
  );
}

/**
 * 그릴 항목. 비면 `SuggestionItems` 가 아무것도 그리지 않는다 (라벨도 서지 않는다).
 * 공백만 있는 항목은 칩이 빈 동그라미가 되어 거른다. 거르기만 하고 합치거나 고치지 않는다 — 정리는 서버 몫이다.
 *
 * 🚨 **제안 문장(`content`)과 같은 항목도 거른다** (#892). Growth 도서는 서버가 `content` 와 `items` 에 같은
 *    "제목(저자)" 를 싣는다 — 일정 초안의 준비물이 그 값이라 서버는 그대로 둔다. 화면에서 거르지 않으면 카드 본문과
 *    "준비물" 칩에 같은 글이 두 번 선다. 제안 문장을 되풀이하는 칩은 어느 Agent 에서도 새로 알려 주는 것이 없어서
 *    도서만 가르지 않고 같은 글이면 거른다. 앞뒤 공백만 무시하고 글자 그대로 비교한다.
 */
export function itemNames(
  agent: Agent,
  items: readonly string[] | undefined,
  content?: string,
): string[] {
  if (ITEM_TERM[agent] === null) return [];
  const sentence = content?.trim();
  return (items ?? [])
    .map((name) => name.trim())
    .filter((name) => name !== "" && name !== sentence);
}

/**
 * Agent 마다 부르는 말. Food 는 장을 볼 것이라 "필요한 재료", 나머지는 챙길 것이라 "준비물".
 * Growth 도서는 항목이 제안 문장과 같은 "제목(저자)" 하나라 영역 자체가 서지 않는다 (`itemNames` · #892).
 * health 는 `null` — 권하는 것이 없어서 준비할 것도 없다 (CLAUDE.md §2 진단하지 않는다).
 */
const ITEM_TERM: Record<Agent, string | null> = {
  food: "필요한 재료",
  activity: "준비물",
  growth: "준비물",
  health: null,
};
