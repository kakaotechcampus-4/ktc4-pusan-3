"use client";

import { ArrowUp, Camera, Mic } from "lucide-react";
import { ViewTransition, type ReactNode } from "react";

import { AgentPrompts } from "@/components/agent-prompts";
import { IconButton } from "@/components/ui/icon-button";
import { ICON_SIZE, ICON_STROKE } from "@/components/ui/icon";
import { TextArea } from "@/components/ui/text-area";
import type { Agent, HomeResponse } from "@/lib/api/types";
import { VIEW_TRANSITION } from "@/lib/view-transition";

/**
 * 03 홈 하단 채팅바 + 그 위의 제안 줄.
 *
 * 부모는 이미 매일 아이 얘기를 하고 있고(배우자에게, 카톡으로) 우리는 **수신처만** 바꾼다
 * (CLAUDE.md §1). 그래서 입력이 폼이 아니라 대화창 모양이다 — 새 습관처럼 보이면 안 된다.
 *
 * 🚨 **사진은 08 화면으로 간다** (`POST /photos` → OCR → 승인 후 commit). 여기서 올리지 않는다 —
 *    올린 뒤 읽어낸 것을 보호자가 확인해야 저장되는데(승인 전 저장 금지), 그 확인은 채팅바 위에
 *    들어갈 크기가 아니다.
 *
 * 🚨 **마이크는 여전히 비활성이다.** 음성의 **외부 전달 범위가 아직 안 정해졌다** (CLAUDE.md §10).
 *    브라우저 음성인식은 기기 안에서 끝나지 않고 음성이 벤더 서버로 나간다 — 미정을 기본값으로
 *    채우지 않는다. 눌리는 것처럼 보이는데 아무 일도 안 일어나는 버튼 대신 **비활성 + 이유**를
 *    남긴다 (00 로그인의 `ready:false` 와 같은 처리).
 *
 * 🚨 **제안 줄은 서버가 고른 것만 그린다** (`agent_prompts` · 시각대 규칙 F-15). 프론트가
 *    무엇을 물을지 고르지 않고, Agent 가 최대 2개라 도메인 색도 자연히 2개를 안 넘는다 (문서 §3).
 *
 * **04 대화 화면도 같은 것을 쓴다** (#226). 두 화면의 채팅바가 한 이름(`VIEW_TRANSITION.chatBar`)으로
 * 이어지는데, 생김새가 다르면 이어 붙이는 동안 모양이 바뀌어 보인다 — 한 컴포넌트라 어긋날 수 없다.
 */
export function HomeComposer({
  value,
  onChange,
  onSubmit,
  prompts,
  onPickPrompt,
  onPickPhoto,
  pending,
  locked = false,
  busyLabel = "보내는 중이에요",
  sentLine = false,
  lead,
}: {
  value: string;
  onChange: (value: string) => void;
  onSubmit: () => void;
  /** 서버가 만든 "지금 도와드릴 수 있는 것". 없으면 줄 자체가 안 나온다. */
  prompts: HomeResponse["agent_prompts"];
  onPickPrompt: (agent: Agent) => void;
  /** 08 사진 화면으로. 🚨 여기서 파일을 고르지 않는다 — 고르는 자리와 확인하는 자리가 같아야 한다. */
  onPickPhoto: () => void;
  /**
   * 보내기만 막는다 — 답을 기다리는 중 (`isBusy`). 🚨 **입력은 열어 둔다** (#231 리뷰). 최대 60초 동안
   * 아무것도 못 쓰면 다음 한 줄을 잊는다 — Claude · ChatGPT 도 막는 것은 보내기뿐이다.
   * 🚨 그래서 입력창에 글이 있는 채로 실패가 오는 일이 흔하다 — 원문을 입력창에 옮기는 쪽이 그 글을
   *    덮지 않아야 한다 (`stores/conversation.ts` 의 `canHandBack`).
   */
  pending: boolean;
  /**
   * 입력까지 통째로 잠근다. 🚨 **03 홈이 막 보내고 대화 화면으로 넘어가는 한 순간에만** 쓴다 — 그때
   * 입력창은 막 보낸 글자를 든 그림이라(`sentLine`) 고칠 수 있으면 안 된다.
   */
  locked?: boolean;
  /** 기다리는 동안 보내기 버튼의 이름. 대화 화면은 보낸 뒤 답을 기다리는 시간이 길다. */
  busyLabel?: string;
  /**
   * 03 홈에서 막 보낸 한 줄을 대화 화면의 말풍선으로 이어 붙일지 (`VIEW_TRANSITION.sentLine`).
   * 🚨 **보낸 순간에만 켠다.** 늘 켜 두면 대화 화면에서 홈으로 돌아올 때 마지막 말풍선이 빈 입력창으로
   *    빨려 들어간다.
   */
  sentLine?: boolean;
  /** 제안 줄 맨 앞에 서는 문 하나 — 03 의 "오늘 대화 이어보기" (`AgentPrompts` 의 `lead`). */
  lead?: ReactNode;
}) {
  const canSubmit = value.trim().length > 0 && !pending && !locked;

  const input = (
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
      // 모바일 키보드의 Enter 자리에 "보내기" 가 뜬다 — 아래 Enter 동작과 같은 말을 한다.
      enterKeyHint="send"
      onKeyDown={(e) => {
        // Enter 는 보내기, Shift+Enter 는 줄바꿈이다 (03 홈 · 04 대화 공통).
        // 🚨 **한글 조합 중의 Enter 는 보내지 않는다.** 그 Enter 는 마지막 글자를 확정하는 키라, 여기서
        //    보내면 끝 글자가 빠진 채 나가거나 조합이 끝나며 한 번 더 나간다 (`isComposing` · 일부 브라우저는 229).
        if (e.key !== "Enter" || e.shiftKey) return;
        if (e.nativeEvent.isComposing || e.keyCode === 229) return;
        e.preventDefault();
        // 보낼 수 없을 때(빈 입력 · 답을 기다리는 중)도 줄바꿈으로 새지 않게 막기만 한다.
        if (canSubmit) onSubmit();
      }}
      disabled={locked}
    />
  );

  return (
    <div className="flex flex-col gap-2">
      {/* 🚨 가로 한 줄로 눕힌다. 세로로 쌓으면 채팅바 위가 두 줄을 먹고, 부모가 제일 자주 여는
          화면에서 **입력창이 그만큼 화면 아래로 밀린다.** 넘치는 만큼은 밀어서 본다 —
          디자인 시스템 §7 이 "칩 줄은 가로 스크롤하지 않는다" 고 못박은 것은 **근거 칩**이고,
          거기서 가리면 부모가 판단할 정보가 사라진다. 이 줄은 들어가는 문이라 밀어도 잃는 게 없다. */}
      <AgentPrompts
        items={prompts}
        onPick={onPickPrompt}
        disabled={locked}
        layout="scroller"
        lead={lead}
      />

      {/* 알약 하나 안에 버튼·입력·보내기가 다 들어간다. 입력만 테두리를 갖지 않는 이유(§7 bare). */}
      <Named name={VIEW_TRANSITION.chatBar}>
        <div className="bg-surface-muted flex items-end gap-1 rounded-full p-1">
          <IconButton label="사진으로 적기" onClick={onPickPhoto} disabled={locked}>
            <Camera aria-hidden size={ICON_SIZE.md} strokeWidth={ICON_STROKE} />
          </IconButton>
          <IconButton label="말로 적기 (음성 처리 방침을 정하는 중이에요)" disabled>
            <Mic aria-hidden size={ICON_SIZE.md} strokeWidth={ICON_STROKE} />
          </IconButton>

          <div className="min-w-0 flex-1">
            {sentLine ? <Named name={VIEW_TRANSITION.sentLine}>{input}</Named> : input}
          </div>

          {/* 🚨 여기에 Spinner 를 넣지 않는다. prefers-reduced-motion 에서는 스피너가 숨는데(문서 §8)
            글자가 없는 버튼이라 빈 원만 남는다 — 기다리는 중이라는 것은 비활성 상태와 이름이 말한다. */}
          <IconButton
            label={pending ? busyLabel : "이 이야기 남기기"}
            tone="brand"
            aria-busy={pending}
            disabled={!canSubmit}
            onClick={onSubmit}
          >
            <ArrowUp aria-hidden size={ICON_SIZE.md} strokeWidth={2} />
          </IconButton>
        </div>
      </Named>
    </div>
  );
}

/**
 * 이름 붙인 전환 한 쌍의 한쪽. 🚨 `default="none"` 과 `share` 를 **같이** 준다 — 이름 붙인 것이
 * 관계없는 전환(다른 화면으로 가는 이동)마다 제 혼자 페이드하지 않게 막고, 쌍이 만나면 이어 붙인다.
 * `share` 가 빠지면 쌍이 조용히 안 이어진다 (`lib/view-transition.ts`).
 */
export function Named({ name, children }: { name: string; children: ReactNode }) {
  return (
    <ViewTransition name={name} share="auto" default="none">
      {children}
    </ViewTransition>
  );
}
