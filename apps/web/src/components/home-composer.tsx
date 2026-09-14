"use client";

import { ArrowUp, Camera, Mic } from "lucide-react";

import { AgentPrompts } from "@/components/agent-prompts";
import { IconButton } from "@/components/ui/icon-button";
import { ICON_SIZE, ICON_STROKE } from "@/components/ui/icon";
import { TextArea } from "@/components/ui/text-area";
import type { Agent, HomeResponse } from "@/lib/api/types";

/**
 * 03 홈 하단 채팅바 + 그 위의 제안 줄.
 *
 * 부모는 이미 매일 아이 얘기를 하고 있고(배우자에게, 카톡으로) 우리는 **수신처만** 바꾼다
 * (CLAUDE.md §1). 그래서 입력이 폼이 아니라 대화창 모양이다 — 새 습관처럼 보이면 안 된다.
 *
 * 🚨 **사진·마이크는 지금 비활성이다.** 사진은 08 화면(`POST /photos` → OCR → 승인 후 commit)이
 *    없으면 올린 뒤 갈 곳이 없고(승인 전 저장 금지), 음성은 **외부 전달 범위가 아직 안 정해졌다**
 *    (CLAUDE.md §10). 브라우저 음성인식은 기기 안에서 끝나지 않고 음성이 벤더 서버로 나간다 —
 *    미정을 기본값으로 채우지 않는다. 눌리는 것처럼 보이는데 아무 일도 안 일어나는 버튼 대신
 *    **비활성 + 이유**를 남긴다 (00 로그인의 `ready:false` 와 같은 처리).
 *
 * 🚨 **제안 줄은 서버가 고른 것만 그린다** (`agent_prompts` · 시각대 규칙 F-15). 프론트가
 *    무엇을 물을지 고르지 않고, Agent 가 최대 2개라 도메인 색도 자연히 2개를 안 넘는다 (문서 §3).
 */
export function HomeComposer({
  value,
  onChange,
  onSubmit,
  prompts,
  onPickPrompt,
  pending,
}: {
  value: string;
  onChange: (value: string) => void;
  onSubmit: () => void;
  /** 서버가 만든 "지금 도와드릴 수 있는 것". 없으면 줄 자체가 안 나온다. */
  prompts: HomeResponse["agent_prompts"];
  onPickPrompt: (agent: Agent) => void;
  pending: boolean;
}) {
  const canSubmit = value.trim().length > 0 && !pending;

  return (
    <div className="flex flex-col gap-2">
      {/* 🚨 가로 한 줄로 눕힌다. 세로로 쌓으면 채팅바 위가 두 줄을 먹고, 부모가 제일 자주 여는
          화면에서 **입력창이 그만큼 화면 아래로 밀린다.** 넘치는 만큼은 밀어서 본다 —
          디자인 시스템 §7 이 "칩 줄은 가로 스크롤하지 않는다" 고 못박은 것은 **근거 칩**이고,
          거기서 가리면 부모가 판단할 정보가 사라진다. 이 줄은 들어가는 문이라 밀어도 잃는 게 없다. */}
      <AgentPrompts items={prompts} onPick={onPickPrompt} disabled={pending} layout="scroller" />

      {/* 알약 하나 안에 버튼·입력·보내기가 다 들어간다. 입력만 테두리를 갖지 않는 이유(§7 bare). */}
      <div className="bg-surface-muted flex items-end gap-1 rounded-full p-1">
        <IconButton label="사진으로 적기 (사진 화면을 만드는 중이에요)" disabled>
          <Camera aria-hidden size={ICON_SIZE.md} strokeWidth={ICON_STROKE} />
        </IconButton>
        <IconButton label="말로 적기 (음성 처리 방침을 정하는 중이에요)" disabled>
          <Mic aria-hidden size={ICON_SIZE.md} strokeWidth={ICON_STROKE} />
        </IconButton>

        <div className="min-w-0 flex-1">
          <TextArea
            label="오늘 있었던 일"
            labelHidden
            variant="bare"
            maxHeightPx={104}
            /* 🚨 한 줄에 들어가는 길이여야 한다. 버튼 3개가 132px 을 가져가서 입력에 남는 폭이
               206px 뿐이고, 이보다 길면 빈 입력창이 두 줄로 선다. 전체 문구는 위의 라벨이 진다. */
            placeholder="말하듯 적어주세요"
            value={value}
            onChange={(e) => onChange(e.target.value)}
            disabled={pending}
          />
        </div>

        {/* 🚨 여기에 Spinner 를 넣지 않는다. prefers-reduced-motion 에서는 스피너가 숨는데(문서 §8)
            글자가 없는 버튼이라 빈 원만 남는다 — 기다리는 중이라는 것은 비활성 상태와 이름이 말한다. */}
        <IconButton
          label={pending ? "보내는 중이에요" : "이 이야기 남기기"}
          tone="brand"
          aria-busy={pending}
          disabled={!canSubmit}
          onClick={onSubmit}
        >
          <ArrowUp aria-hidden size={ICON_SIZE.md} strokeWidth={2} />
        </IconButton>
      </div>
    </div>
  );
}
