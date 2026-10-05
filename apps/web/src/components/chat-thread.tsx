"use client";

import { Named } from "@/components/home-composer";
import { ConsentRequiredCard } from "@/components/consent-required-card";
import { RunReply } from "@/components/run-result";
import { Button } from "@/components/ui/button";
import { CardFailed } from "@/components/ui/card";
import { isApiError, type Agent } from "@/lib/api";
import { formatDay } from "@/lib/format";
import { VIEW_TRANSITION } from "@/lib/view-transition";
import {
  answerQuestion,
  canRetry,
  retryTurn,
  rewriteTurn,
  type ChatQuestion,
  type ChatTurn,
} from "@/stores/conversation";
import { NO_DRAFTS, useEventDraftStore } from "@/stores/event-draft";

/**
 * 04 대화 — 보낸 한 줄과 그 답을 하루 단위로 쌓는다 (#226).
 *
 * 🚨 **실패를 그리는 자리는 보낸 말풍선 바로 아래 한 곳이다.** 홈에서 보냈든 여기서 보냈든
 *    같다 — 예전엔 03 홈 채팅바 위(`SubmitErrorCard`)와 04 결과 화면 두 곳이라, 같은 실패가
 *    어디서 보냈는지에 따라 다른 자리 · 다른 문구로 섰다.
 * 🚨 **하루가 한 덩어리다.** 날짜 경계는 한국 시간 자정이고 코드가 정한다 (`ChatTurn.day`).
 *    자정을 넘겨 온 답은 질문이 나온 날에 붙으므로, 날짜 머리줄 아래 순서는 보낸 순서와 다를 수 있다.
 */
export function ChatThread({
  childId,
  turns,
  answering,
  onOpenDrafts,
  onPickOffer,
}: {
  childId: string;
  turns: ChatTurn[];
  /** 입력창이 지금 답하는 질문 — 그 질문의 카드가 "답하는 중" 으로 바뀐다. */
  answering: ChatQuestion | null;
  onOpenDrafts: (turn: ChatTurn) => void;
  onPickOffer: (agents: Agent[], runId: string) => void;
}) {
  const last = turns.at(-1);
  const days = groupByDay(turns);
  /** 하루뿐이면 머리(`chat/page.tsx`)가 날짜를 이미 말한다 — 같은 날짜를 두 번 세우지 않는다. */
  const dividers = days.length > 1;

  return (
    <div className="flex flex-col gap-8">
      {days.map(([day, dayTurns]) => (
        <section key={day} aria-label={`${formatDay(day)} 대화`} className="flex flex-col gap-6">
          {dividers ? (
            <h2 className="text-caption text-ink-subtle text-center">{formatDay(day)}</h2>
          ) : null}
          <ol className="flex flex-col gap-6">
            {dayTurns.map((turn) => (
              <li key={turn.id} className="flex flex-col gap-3">
                <SentBubble turn={turn} named={turn.id === last?.id} />
                {turn.send.status === "error" ? (
                  <SendFailure childId={childId} turn={turn} error={turn.send.error} />
                ) : (
                  <TurnReply
                    childId={childId}
                    turn={turn}
                    answering={answering}
                    onOpenDrafts={() => onOpenDrafts(turn)}
                    onPickOffer={(agents) => {
                      if (turn.send.status === "sent") onPickOffer(agents, turn.send.runId);
                    }}
                  />
                )}
              </li>
            ))}
          </ol>
        </section>
      ))}
    </div>
  );
}

/** 보낸 순서를 지키면서 날짜로 묶는다. 날짜는 오래된 것부터. */
function groupByDay(turns: ChatTurn[]): Array<[string, ChatTurn[]]> {
  const days = new Map<string, ChatTurn[]>();
  for (const turn of turns) {
    const list = days.get(turn.day);
    if (list) list.push(turn);
    else days.set(turn.day, [turn]);
  }
  return [...days.entries()].sort(([a], [b]) => a.localeCompare(b));
}

/**
 * 부모가 보낸 한 줄. 🚨 **원문의 자리다** — 서버가 끝을 말하기 전에는 사라지지 않는다
 * (`stores/conversation.ts`).
 *
 * 🚨 **`brand` 면 + 흰 글자다** (디자인 시스템 §2-2 의 여섯째 자리). 이 화면의 주인공은 답 묶음이 아니라
 *    **부모가 적은 말**이다 (예전 04 에서 `card-accent` 한 장을 받던 "적어주신 한 줄"). 답 묶음이 전부
 *    `surface` 카드라서, 보낸 한 줄까지 뉴트럴이면 주고받음 하나가 어디서 시작되는지가 안 보인다.
 *    🚨 **꼬리 · 그림자를 달지 않는다** — 주 버튼과 같은 색이라, 누를 수 있는 것처럼 보이지 않게
 *    모양은 카드 그대로 두고 누르는 반응도 주지 않는다.
 *
 * @param named 막 보낸 한 줄인가. 🚨 이름은 **하나에만** 준다 — 같은 이름이 둘이면 전환이 건너뛴다.
 */
function SentBubble({ turn, named }: { turn: ChatTurn; named: boolean }) {
  const bubble = (
    // 흰 글자 6.2:1 (§2-2 `brand` 행) — 캡션도 같은 흰색이다. 흐리게 낮추면 4.5:1 아래로 내려간다.
    <div className="bg-brand rounded-card max-w-[85%] px-4 py-3 text-white">
      {turn.answering ? <p className="text-caption mb-1">질문에 대한 답</p> : null}
      <p className="text-body break-words whitespace-pre-wrap">{turn.body.text}</p>
    </div>
  );

  return (
    <div className="flex justify-end">
      {named ? <Named name={VIEW_TRANSITION.sentLine}>{bubble}</Named> : bubble}
    </div>
  );
}

function TurnReply({
  childId,
  turn,
  answering,
  onOpenDrafts,
  onPickOffer,
}: {
  childId: string;
  turn: ChatTurn;
  answering: ChatQuestion | null;
  onOpenDrafts: () => void;
  onPickOffer: (agents: Agent[]) => void;
}) {
  const runId = turn.send.status === "sent" ? turn.send.runId : null;
  const stored = useEventDraftStore((s) => s.byChild[childId] ?? NO_DRAFTS);
  const remainingDrafts = stored.filter(
    (d) => d.origin === "input" && turn.run.drafts.some((r) => r.draft_id === d.draft.draft_id),
  ).length;

  return (
    <RunReply
      childId={childId}
      run={turn.run}
      question={turn.question}
      answeringNow={runId !== null && answering?.runId === runId}
      remainingDrafts={remainingDrafts}
      onRetry={canRetry(turn) ? () => retryTurn(childId, turn.id) : undefined}
      onRewrite={turn.rewritten ? undefined : () => rewriteTurn(childId, turn.id)}
      onAnswer={() => {
        if (runId) answerQuestion(childId, runId);
      }}
      onOpenDrafts={onOpenDrafts}
      onPickOffer={onPickOffer}
    />
  );
}

/**
 * 서버에 **닿지 못한** 실패 — 202 를 못 받았다. 400 · 429 · 403 은 서버가 거절한 것이고, 네트워크 ·
 * 5xx 는 서버가 받았는데 응답만 잃었을 수 있다 (그래서 그 경우의 다시 시도는 같은 키다).
 *
 * 🚨 **다시 시도 · 고쳐 쓰기가 이 말풍선을 대체한다** (`isUnreached`). 대화에 남길 것이 없다.
 * 🚨 **`daily_input_limit` · `reply_context_unavailable` 에는 "다시 시도" 를 두지 않는다**
 *    (#147 · #175). 같은 본문은 같은 키로 나가서 몇 번을 눌러도 같은 답이다 — 누르면 같은 실패가
 *    나오는 버튼을 만들지 않는다 (`consent_required` 를 일반 실패로 그리지 않는 것과 같은 이유).
 * 🚨 **429 · 400 문구는 서버 것을 그대로 쓴다.** 하루 몇 번인지는 서버 설정값이라 바뀐다 (#147).
 * 🚨 **"다 다시 적어 주세요" 라고 하지 않는다** (#158 · #175 · #208). 앞서 저장된 이야기까지 다시
 *    적으면 그 조각이 두 번 저장된다.
 */
function SendFailure({
  childId,
  turn,
  error,
}: {
  childId: string;
  turn: ChatTurn;
  error: unknown;
}) {
  if (isApiError(error, "consent_required")) {
    return (
      <div className="flex flex-col gap-2">
        {/* 🚨 403 을 일반 실패로 그리면 "다시 시도" 만 누르게 된다 — 다시 시도해도 같은 403 이다. */}
        <ConsentRequiredCard
          childId={childId}
          error={error}
          what="적어주신 말을 저장할 수 없어요."
        />
        <p className="text-caption text-ink-subtle">적어주신 말은 입력창에 그대로 남겨뒀어요.</p>
      </div>
    );
  }

  if (isApiError(error, "daily_input_limit")) {
    return (
      <CardFailed>
        <p>{error.message}</p>
        {/* 🚨 원문은 입력창에 되돌려 뒀다 — 내일 보낼 한 줄이다 (`failSend`). */}
        <p className="text-caption text-ink-subtle mt-2">
          적어주신 말은 입력창에 그대로 남겨뒀어요.
        </p>
      </CardFailed>
    );
  }

  if (isApiError(error, "reply_context_unavailable")) {
    return (
      <CardFailed>
        <p>{error.message}</p>
        {/* 무엇을 물었는지 이 자리가 대신 든다 — 입력창의 "3일 전부터" 만으로는 보호자도 무엇에
            대한 답이었는지 다시 적을 수 없다. 🚨 LLM 이 만든 문장이라 HTML 로 그리지 않는다. */}
        {turn.answering ? (
          <p className="text-body-sm text-ink mt-2">{turn.answering.text}</p>
        ) : null}
        <p className="text-caption text-ink-subtle mt-2">
          고쳐 쓰기를 누르면 적어주신 답이 입력창으로 돌아가요. 무엇에 대한 답인지 함께 적어 보내
          주세요. 앞서 저장된 이야기는 다시 적지 않아도 돼요.
        </p>
        <Button
          variant="tertiary"
          size="compact"
          className="mt-3"
          onClick={() => rewriteTurn(childId, turn.id)}
        >
          고쳐 쓰기
        </Button>
      </CardFailed>
    );
  }

  return (
    <CardFailed>
      {/* 🚨 "아무것도 저장되지 않았어요" 라고 하지 않는다 — 서버가 받았는데 응답만 잃었을 수 있다
          (PR #71 리뷰). 그래서 같은 키로 다시 보내는 길이 먼저다. */}
      <p>
        보내지 못했어요. 다시 시도하면 같은 한 줄로 보내요. 이미 닿았다면 두 번 저장되지 않아요.
      </p>
      <div className="mt-3 flex flex-wrap gap-2">
        {/* 같은 키로 나가는 재시도라 실제로 두 번 저장되지 않는다 (lib/api/idempotency.ts). */}
        <Button variant="secondary" size="compact" onClick={() => retryTurn(childId, turn.id)}>
          다시 시도
        </Button>
        <Button variant="tertiary" size="compact" onClick={() => rewriteTurn(childId, turn.id)}>
          고쳐 쓰기
        </Button>
      </div>
    </CardFailed>
  );
}
