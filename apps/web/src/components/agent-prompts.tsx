"use client";

import { DomainChip, domainPress } from "@/components/domain-chip";
import type { Agent } from "@/lib/api/types";
import { cn } from "@/lib/cn";

/**
 * "이 주제로 도와드릴까요" 줄. 서버가 시각대 규칙(F-15)으로 고른 것만 그린다 — 프론트가
 * 무엇을 물을지 고르지 않는다.
 *
 * 🚨 **버튼처럼 쌓지 않는다.** 나란히 놓인 선택지를 버튼으로 세우면 무엇이 다음 행동인지가
 *    사라진다 (문서 §7 "한 화면에 primary 는 하나"). 이건 고르는 줄이고, 화면을 떠나는 버튼과
 *    같은 무게로 보이면 안 된다.
 *
 * 🚨 **도메인 색은 칩이 지고 뜻은 그 옆의 글자가 진다** (문서 §3). 최대 2개만 그린다 —
 *    한 화면에 도메인 색은 2개까지고, Agent 도 최대 2개다 (NF-01).
 *
 * 배치는 두 가지다.
 * - `list` — 세로로 쌓는다. 04 저장 결과처럼 **화면에 자리가 있는** 곳.
 * - `scroller` — 가로 한 줄로 눕히고 넘치면 밀어서 본다. 03 채팅바 위처럼 **세로가 비싼** 곳.
 */
const MAX_PROMPTS = 2;

export interface AgentPrompt {
  agent: Agent;
  text: string;
}

export function AgentPrompts({
  items,
  onPick,
  disabled,
  layout,
}: {
  items: AgentPrompt[];
  onPick: (agent: Agent) => void;
  disabled?: boolean;
  layout: "list" | "scroller";
}) {
  const shown = items.slice(0, MAX_PROMPTS);
  if (shown.length === 0) return null;

  if (layout === "list") {
    return (
      <ul className="flex flex-col gap-1.5">
        {shown.map((prompt) => (
          <li key={prompt.agent}>
            <PromptButton prompt={prompt} onPick={onPick} disabled={disabled} block />
          </li>
        ))}
      </ul>
    );
  }

  return (
    // 🚨 좌우 여백을 상쇄해 **화면 끝까지** 흐르게 한다. 안쪽에서 잘리면 밀 수 있다는 것이
    //    안 보이고, 마지막 줄이 여백에 걸려 잘린 것처럼 읽힌다. 상쇄값은 `Screen` 의
    //    하단 바 여백과 같은 값이어야 한다 (px-3 / 380px 이상 px-4).
    //
    // 🚨 `overscroll-x-contain` — 웹뷰에서 가로 스와이프가 끝까지 가면 네이티브 뒤로가기
    //    제스처로 넘어간다. 제안을 밀다가 화면이 뒤로 가면 안 된다.
    <ul className="-mx-3 flex gap-2 overflow-x-auto overscroll-x-contain px-3 min-[380px]:-mx-4 min-[380px]:px-4">
      {shown.map((prompt) => (
        <li key={prompt.agent} className="shrink-0">
          <PromptButton prompt={prompt} onPick={onPick} disabled={disabled} />
        </li>
      ))}
    </ul>
  );
}

function PromptButton({
  prompt,
  onPick,
  disabled,
  block = false,
}: {
  prompt: AgentPrompt;
  onPick: (agent: Agent) => void;
  disabled?: boolean;
  block?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={() => onPick(prompt.agent)}
      disabled={disabled}
      className={cn(
        "border-line bg-surface disabled:bg-surface-muted ease-standard min-h-touch flex items-center gap-2 rounded-full border px-2 py-1.5 text-left transition-colors duration-120",
        // 🚨 누르면 **그 주제가 열릴 색**이다 (05 제안 줄과 같은 규칙). 뉴트럴 틴트로 누르면
        //    눌린 색과 다음 화면의 색이 달라서 두 동작이 남남처럼 보인다.
        domainPress(prompt.agent),
        block ? "w-full" : "whitespace-nowrap",
      )}
    >
      <DomainChip agent={prompt.agent} />
      <span className="text-body-sm text-ink-muted min-w-0">{prompt.text}</span>
    </button>
  );
}
