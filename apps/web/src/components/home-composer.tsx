"use client";

import { ArrowUp, Camera, Mic } from "lucide-react";
import type { ReactNode } from "react";

import { AgentPrompts } from "@/components/agent-prompts";
import { IconButton } from "@/components/ui/icon-button";
import { Named } from "@/components/ui/named";
import { ICON_SIZE, ICON_STROKE } from "@/components/ui/icon";
import { TextArea } from "@/components/ui/text-area";
import { useVoiceInput } from "@/hooks/use-voice-input";
import type { HomeResponse } from "@/lib/api/types";
import { PENDING_SERVER_FEATURES } from "@/lib/features";
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
 * 🚨 **마이크는 앱의 기기 안 음성인식으로만 켠다** (#595 · `lib/native/speech.ts`). 음성을 서비스 밖으로
 *    보내는 것은 정한 적이 없다 — 브라우저 음성인식은 음성을 벤더 서버로 보내서 쓰지 않는다 (#598).
 *    브라우저와 기기 안 인식을 못 하는 휴대폰에서는 눌리는 것처럼 보이는데 아무 일도 안 일어나는 버튼 대신
 *    **비활성 + 이유**를 남긴다 (00 로그인의 `ready:false` 와 같은 처리). 들은 말은 입력창에 넣기만 하고
 *    보내기는 보호자가 누른다.
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
  promptsPending = false,
}: {
  value: string;
  onChange: (value: string) => void;
  onSubmit: () => void;
  /** 서버가 만든 "지금 도와드릴 수 있는 것". 없으면 줄 자체가 안 나온다. */
  prompts: HomeResponse["agent_prompts"];
  /** 🚨 고른 줄을 통째로 받는다 — 05 가 그 줄의 `prompt_key` 를 추천 요청에 싣는다 (#286). */
  onPickPrompt: (prompt: HomeResponse["agent_prompts"][number]) => void;
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
  /** 제안을 아직 받는 중. 줄 자리를 알약 모양으로 잡아 둔다 (`AgentPrompts` 의 `pending`). */
  promptsPending?: boolean;
}) {
  const canSubmit = value.trim().length > 0 && !pending && !locked;
  const voice = useVoiceInput({ value, onChange });

  // 보내면 듣기를 버린다 — 그 뒤에 오는 글자가 비운 입력창을 다시 채우면 안 된다 (`useVoiceInput`).
  const submit = () => {
    voice.cancel();
    onSubmit();
  };

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
        if (canSubmit) submit();
      }}
      disabled={locked}
      // 듣는 동안 고친 글은 다음에 들은 말이 덮는다 (`useVoiceInput`).
      readOnly={voice.listening}
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
        pending={promptsPending}
      />

      {/* 듣는 중이라는 것과 못 들은 이유를 알약 바로 위에 둔다. 🚨 실패를 빨강으로 칠하지 않는다 (디자인 시스템 §2).
          🚨 리전은 빈 채로 늘 세워 둔다 — 글자와 함께 생기면 스크린리더가 첫 안내를 안 읽는다 (디자인 시스템 §10).
             `hidden` 으로 숨기지 않는 것도 같은 이유다(숨기면 트리에서 빠진다). 위 `gap` 밖의 묶음에 넣어서
             비어 있을 때는 높이도 간격도 0 이고, 글자가 있을 때만 알약과 띄운다. */}
      <div>
        <p role="status" className="text-body-sm text-ink-muted px-3 [&:not(:empty)]:pb-2">
          {voice.listening ? "듣고 있어요. 다 말했으면 마이크를 다시 눌러 주세요." : voice.notice}
        </p>

        {/* 알약 하나 안에 버튼·입력·보내기가 다 들어간다. 입력만 테두리를 갖지 않는 이유(§7 bare). */}
        <Named name={VIEW_TRANSITION.chatBar}>
          <div className="bg-surface-muted flex items-end gap-1 rounded-full p-1">
            {/* 🚨 마이크처럼 비활성으로 세우지 않고 숨긴다 — 이유를 말할 수 있는 미정(음성 방침)이 아니라
              서버에 아직 없는 기능이라, 보호자에게 남길 설명이 없다 (`lib/features.ts` · #409). */}
            {PENDING_SERVER_FEATURES.photoEntry ? (
              <IconButton label="사진으로 적기" onClick={onPickPhoto} disabled={locked}>
                <Camera aria-hidden size={ICON_SIZE.md} strokeWidth={ICON_STROKE} />
              </IconButton>
            ) : null}
            {voice.support === "ready" ? (
              // 이름은 그대로 두고 켜짐은 `aria-pressed` 가 말한다 — 이름까지 바꾸면 "그만 듣기, 눌림" 처럼 겹쳐 읽힌다.
              <IconButton
                label="말로 적기"
                aria-pressed={voice.listening}
                onClick={voice.toggle}
                disabled={locked}
              >
                <Mic aria-hidden size={ICON_SIZE.md} strokeWidth={ICON_STROKE} />
              </IconButton>
            ) : (
              <IconButton
                label={
                  voice.support === "none"
                    ? "말로 적기 (앱에서 쓸 수 있어요)"
                    : "말로 적기 (이 휴대폰에서는 쓸 수 없어요)"
                }
                disabled
              >
                <Mic aria-hidden size={ICON_SIZE.md} strokeWidth={ICON_STROKE} />
              </IconButton>
            )}

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
              onClick={submit}
            >
              <ArrowUp aria-hidden size={ICON_SIZE.md} strokeWidth={2} />
            </IconButton>
          </div>
        </Named>
      </div>
    </div>
  );
}
