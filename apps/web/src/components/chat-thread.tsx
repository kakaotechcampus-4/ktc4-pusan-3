"use client";

import { Named } from "@/components/ui/named";
import { Swap } from "@/components/ui/swap";
import { ConsentRequiredCard } from "@/components/consent-required-card";
import { RewriteButton } from "@/components/rewrite-button";
import { RunReply } from "@/components/run-result";
import { Button } from "@/components/ui/button";
import { CardFailed } from "@/components/ui/card";
import { initialRunState } from "@/hooks/use-run-stream";
import { isApiError, type Agent } from "@/lib/api";
import type { PastTurn } from "@/lib/chat-history";
import { formatDay } from "@/lib/format";
import { VIEW_TRANSITION } from "@/lib/view-transition";
import { useDraftText } from "@/stores/draft";
import {
  answerQuestion,
  canRetry,
  isRejected,
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
 *
 * ## 지난 대화와 합치기 (#228)
 *
 * 🚨 **두 곳에서 온 줄을 `run_id` 로 합친다.** `past` 는 서버가 남긴 턴이고 `turns` 는 이 앱에서 보낸 줄이다.
 *    run 이 끝나면 서버도 그 턴을 남겨서 같은 run 이 두 곳에 있게 되는데, 🚨 **이 앱에서 보낸 줄이 정본이다** —
 *    진행 중이거나 다시 시도할 수 있는 줄이고, 서버 쪽은 끝난 run 만 남긴다. 그래서 겹치면 지난 쪽을 뺀다.
 * 🚨 **같은 날 안에서는 지난 줄이 먼저다.** 이 앱에서 보낸 줄은 앱을 켠 뒤의 것이라 늘 더 늦다.
 * 🚨 **지난 줄의 날짜는 서버가 정한 것이다**(`PastTurn.day`). 화면이 접수 시각으로 다시 계산하지 않는다.
 *
 * `onOpenDrafts` 는 일정 초안 시트를 그 답의 초안으로 연다 — `key` 는 시트를 답마다 새로 그리는 값이다.
 */
export function ChatThread({
  childId,
  past,
  turns,
  answering,
  onOpenDrafts,
  onPickOffer,
}: {
  childId: string;
  /** 서버가 남긴 지난 턴, 오래된 순 (`useChatHistory`). */
  past: readonly PastTurn[];
  turns: ChatTurn[];
  /** 입력창이 지금 답하는 질문 — 그 질문의 카드가 "답하는 중" 으로 바뀐다. */
  answering: ChatQuestion | null;
  onOpenDrafts: (key: string, draftIds: string[]) => void;
  onPickOffer: (agents: Agent[]) => void;
}) {
  const last = turns.at(-1);
  const days = groupByDay(mergeEntries(past, turns));
  /** 하루뿐이면 머리(`chat/page.tsx`)가 날짜를 이미 말한다 — 같은 날짜를 두 번 세우지 않는다. */
  const dividers = days.length > 1;
  /** 누가 답했는지 — 지난 질문은 뒤에 답한 줄이 있으면 "답함", 없으면 "닫힘" 이다. */
  const answered = new Set<string>([
    ...past.flatMap((t) => (t.replyTo ? [t.replyTo] : [])),
    ...turns.flatMap((t) => (t.body.reply_to ? [t.body.reply_to] : [])),
  ]);

  return (
    <div className="flex flex-col gap-8">
      {days.map(([day, entries]) => (
        <section
          key={day}
          id={chatDayAnchor(day)}
          // 머리(`chat/page.tsx`)가 지금 보고 있는 날을 찾을 때 읽는다.
          data-chat-day={day}
          aria-label={`${formatDay(day)} 대화`}
          // 캘린더에서 그날로 열 때 이 자리로 내린다 — 붙어 있는 머리(`Screen` 의 topBar)에 날짜가 가리지 않게 띄운다.
          className="flex scroll-mt-24 flex-col gap-6"
        >
          {dividers ? (
            <h2 className="text-caption text-ink-subtle text-center">{formatDay(day)}</h2>
          ) : null}
          <ol className="flex flex-col gap-6">
            {entries.map((entry) =>
              entry.kind === "past" ? (
                <li key={`past:${entry.turn.runId}`} className="flex flex-col gap-3">
                  <PastTurnItem
                    childId={childId}
                    turn={entry.turn}
                    answered={answered}
                    answering={answering}
                    onOpenDrafts={onOpenDrafts}
                  />
                </li>
              ) : (
                <LiveTurnItem
                  key={entry.turn.id}
                  childId={childId}
                  turn={entry.turn}
                  named={entry.turn.id === last?.id}
                  later={turns.slice(turns.indexOf(entry.turn) + 1)}
                  answering={answering}
                  onOpenDrafts={onOpenDrafts}
                  onPickOffer={onPickOffer}
                />
              ),
            )}
          </ol>
        </section>
      ))}
    </div>
  );
}

/** 캘린더 입구가 그날로 내릴 때 찾는 자리 (`chat/page.tsx`). */
export function chatDayAnchor(day: string): string {
  return `chat-day-${day}`;
}

type Entry = { kind: "past"; turn: PastTurn } | { kind: "live"; turn: ChatTurn };

/** 위 "지난 대화와 합치기" — 겹치는 run 은 이 앱의 줄만 남기고, 같은 날 안에서는 지난 줄이 먼저다. */
function mergeEntries(past: readonly PastTurn[], turns: ChatTurn[]): Entry[] {
  const live = new Set(turns.flatMap((t) => (t.send.status === "sent" ? [t.send.runId] : [])));
  return [
    ...past.filter((t) => !live.has(t.runId)).map((turn) => ({ kind: "past" as const, turn })),
    ...turns.map((turn) => ({ kind: "live" as const, turn })),
  ];
}

/**
 * 지난 턴 하나 (#228). 🚨 **답 묶음은 그날과 같은 `RunReply` 다** — `archived` 가 지금 누르면 막다른 길이 되는
 * 버튼만 뺀다.
 *
 * - 되묻기: 뒤에 답한 줄이 있으면 "답함". 🚨 **서버가 아직 답할 수 있다고 하면(`reply_open`) 열린 질문이다** —
 *   앱을 다시 켠 지 15분이 안 된 경우다. 그 밖에는 "닫힘" 이다(맥락이 없어 `reply_to` 로 보내면 400).
 * - 일정 초안: 🚨 **보관함(`stores/event-draft.ts`)에 아직 남은 초안이면 이어서 넣는다.** 보관함은 탭이 살아 있는
 *   동안 남고(새로고침 포함) 넣으면 빠지므로, 남아 있으면 아직 넣지 않은 것이다. 없으면 그때 내용만 읽기 전용이다.
 */
function PastTurnItem({
  childId,
  turn,
  answered,
  answering,
  onOpenDrafts,
}: {
  childId: string;
  turn: PastTurn;
  answered: ReadonlySet<string>;
  answering: ChatQuestion | null;
  onOpenDrafts: (key: string, draftIds: string[]) => void;
}) {
  const stored = useEventDraftStore((s) => s.byChild[childId] ?? NO_DRAFTS);
  const draftIds = turn.run.drafts.map((d) => d.draft_id);
  const remainingDrafts = stored.filter(
    (d) => d.origin === "input" && draftIds.includes(d.draft.draft_id),
  ).length;
  const note = turn.run.note;
  const asked = note?.kind === "question";
  const question = !asked
    ? null
    : answered.has(turn.runId)
      ? "answered"
      : turn.replyOpen
        ? "open"
        : "dropped";

  return (
    <>
      <SentBubble text={turn.text} answer={turn.replyTo !== null} named={false} />
      <RunReply
        childId={childId}
        runId={turn.runId}
        run={turn.run}
        question={question}
        answeringNow={answering?.runId === turn.runId}
        remainingDrafts={remainingDrafts}
        draftsReplaced={false}
        followUp={turn.replyTo !== null}
        rewriteBlocked={false}
        onAnswer={() => {
          if (note) answerQuestion(childId, turn.runId, { text: note.text, day: turn.day });
        }}
        onOpenDrafts={() => onOpenDrafts(`past:${turn.runId}`, draftIds)}
        onPickOffer={noop}
        archived
        deleted={turn.deleted}
      />
    </>
  );
}

function noop() {}

function LiveTurnItem({
  childId,
  turn,
  named,
  later,
  answering,
  onOpenDrafts,
  onPickOffer,
}: {
  childId: string;
  turn: ChatTurn;
  named: boolean;
  later: ChatTurn[];
  answering: ChatQuestion | null;
  onOpenDrafts: (key: string, draftIds: string[]) => void;
  onPickOffer: (agents: Agent[]) => void;
}) {
  return (
    <li className="flex flex-col gap-3">
      <SentBubble text={turn.body.text} answer={turn.answering !== null} named={named} />
      {turn.send.status === "error" ? (
        <SendFailure childId={childId} turn={turn} error={turn.send.error} />
      ) : (
        <TurnReply
          childId={childId}
          turn={turn}
          later={later}
          answering={answering}
          onOpenDrafts={() =>
            onOpenDrafts(
              turn.id,
              turn.run.drafts.map((d) => d.draft_id),
            )
          }
          onPickOffer={(agents) => {
            // 🚨 run 을 싣지 않는다 — 04 제안은 처음 요청이다 (`chat/page.tsx` 의 goToSuggestions).
            if (turn.send.status === "sent") onPickOffer(agents);
          }}
        />
      )}
    </li>
  );
}

/** 보낸 순서를 지키면서 날짜로 묶는다. 날짜는 오래된 것부터. */
function groupByDay(entries: Entry[]): Array<[string, Entry[]]> {
  const days = new Map<string, Entry[]>();
  for (const entry of entries) {
    const list = days.get(entry.turn.day);
    if (list) list.push(entry);
    else days.set(entry.turn.day, [entry]);
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
 * @param answer 되묻기에 답한 줄인가.
 */
function SentBubble({ text, answer, named }: { text: string; answer: boolean; named: boolean }) {
  const bubble = (
    // 흰 글자 6.2:1 (§2-2 `brand` 행) — 캡션도 같은 흰색이다. 흐리게 낮추면 4.5:1 아래로 내려간다.
    <div className="bg-brand rounded-card max-w-[85%] px-4 py-3 text-white">
      {answer ? <p className="text-caption mb-1">질문에 대한 답</p> : null}
      <p className="text-body break-words whitespace-pre-wrap">{text}</p>
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
  later,
  answering,
  onOpenDrafts,
  onPickOffer,
}: {
  childId: string;
  turn: ChatTurn;
  /** 이 줄 뒤에 보낸 줄들 — 뒤 run 이 같은 일정 초안을 다시 냈는지 본다. */
  later: ChatTurn[];
  answering: ChatQuestion | null;
  onOpenDrafts: () => void;
  onPickOffer: (agents: Agent[]) => void;
}) {
  const runId = turn.send.status === "sent" ? turn.send.runId : null;
  const rewriteBlocked = useRewriteBlocked(childId);
  const stored = useEventDraftStore((s) => s.byChild[childId] ?? NO_DRAFTS);
  const remainingDrafts = stored.filter(
    (d) => d.origin === "input" && turn.run.drafts.some((r) => r.draft_id === d.draft.draft_id),
  ).length;
  const draftsReplaced = replacedByLater(turn, later);
  // 🚨 다시 보내는 동안에는 진행 표시다. 앞 run 은 지우지 않고 들고 있다가 202 를 받으면 갈아 끼운다
  //    — 다시 시도가 실패해도 "여기까지 받은 기록" 이 남는다 (`stores/conversation.ts` 의 `submit`).
  const run = turn.send.status === "sending" ? SENDING : turn.run;
  const running = run.status === "idle" || run.status === "streaming";

  return (
    // 진행 표시 → 결과는 같은 자리의 교체다 (디자인 시스템 §8). 🚨 **말풍선이 붙는 것에는 움직임이 없다** —
    //    이건 이미 서 있던 진행 카드가 결과로 바뀌는 순간뿐이다 (§8 "하지 않는 것").
    <Swap id={running ? "progress" : "result"}>
      <RunReply
        childId={childId}
        runId={runId}
        run={run}
        question={turn.question}
        answeringNow={runId !== null && answering?.runId === runId}
        remainingDrafts={remainingDrafts}
        draftsReplaced={draftsReplaced}
        // 이어받기 run 이 또 물은 질문이다 — "한 가지만 더" 를 두 번 말하지 않는다.
        followUp={turn.answering !== null}
        onRetry={canRetry(turn) ? () => retryTurn(childId, turn.id) : undefined}
        onRewrite={turn.handedBack ? undefined : () => rewriteTurn(childId, turn.id)}
        rewriteBlocked={rewriteBlocked}
        onAnswer={() => {
          if (runId) answerQuestion(childId, runId);
        }}
        onOpenDrafts={onOpenDrafts}
        onPickOffer={onPickOffer}
      />
    </Swap>
  );
}

/**
 * 이 줄의 일정 초안을 뒤 run 이 **같은 초안으로 다시 냈는가**. 스토어가 같은 `draft_id` · `event_id` 를 새 장으로
 * 갈아 끼우므로(`mergeDrafts`), 그때 이 줄의 남은 초안이 0 이 되는데 그건 "넣었다" 가 아니다 (#231 리뷰).
 */
function replacedByLater(turn: ChatTurn, later: ChatTurn[]): boolean {
  const ids = new Set(turn.run.drafts.map((d) => d.draft_id));
  const events = new Set(
    turn.run.drafts.map((d) => d.event_id).filter((id): id is string => id !== null),
  );
  return later.some((t) =>
    t.run.drafts.some(
      (d) => ids.has(d.draft_id) || (d.event_id !== null && events.has(d.event_id)),
    ),
  );
}

/** 보내는 동안 그리는 run. 진행 표시 첫 칸이다. */
const SENDING = { ...initialRunState, status: "streaming" as const };

/**
 * "고쳐 쓰기" 를 지금 못 하는가 — 입력창에 쓰던 글이 있다 (`canHandBack`).
 * 🚨 덮으면 그 글이 사라지고, 안 덮고 말풍선만 지우면 이 글이 사라진다 (#231 리뷰). 그래서 잠그고 이유를 말한다.
 */
function useRewriteBlocked(childId: string): boolean {
  const [draft] = useDraftText(childId);
  return draft.trim().length > 0;
}

/**
 * 202 를 못 받은 실패. 400 · 429 · 403 은 서버가 거절한 것이고, 네트워크 · 5xx 는 서버가 받았는데
 * 응답만 잃었을 수 있다 (그래서 그 경우의 다시 시도는 같은 키다).
 *
 * 🚨 **이 말풍선은 원문을 입력창에 옮긴 뒤에만 사라진다** (`isReplaceable` · #231 리뷰). 네트워크 · 5xx
 *    실패를 다음 한 줄이 대체하면, 보낼 때 입력창을 비웠으니 원문이 어디에도 안 남는다.
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
  const rewriteBlocked = useRewriteBlocked(childId);
  const rewrite = () => rewriteTurn(childId, turn.id);
  /** 429 · 403 에서 원문을 입력창에 옮겼는가 — 못 옮겼으면 말풍선이 원문의 자리다 (`failSend`). */
  const keptHere = !turn.handedBack;

  if (isApiError(error, "consent_required")) {
    return (
      <div className="flex flex-col gap-2">
        {/* 🚨 403 을 일반 실패로 그리면 "다시 시도" 만 누르게 된다 — 다시 시도해도 같은 403 이다. */}
        <ConsentRequiredCard
          childId={childId}
          error={error}
          what="적어주신 말을 저장할 수 없어요."
        />
        {keptHere ? (
          <RewriteButton blocked={rewriteBlocked} onClick={rewrite} />
        ) : (
          <p className="text-caption text-ink-subtle">적어주신 말은 입력창에 그대로 남겨뒀어요.</p>
        )}
      </div>
    );
  }

  if (isApiError(error, "daily_input_limit")) {
    return (
      <CardFailed>
        <p>{error.message}</p>
        {/* 🚨 원문은 입력창에 되돌려 뒀다 — 내일 보낼 한 줄이다 (`failSend`). 입력창에 쓰던 글이 있어서
            못 옮겼으면 여기서 꺼내 간다. */}
        {keptHere ? (
          <div className="mt-3">
            <RewriteButton blocked={rewriteBlocked} onClick={rewrite} />
          </div>
        ) : (
          <p className="text-caption text-ink-subtle mt-2">
            적어주신 말은 입력창에 그대로 남겨뒀어요.
          </p>
        )}
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
        <div className="mt-3">
          <RewriteButton blocked={rewriteBlocked} onClick={rewrite} />
        </div>
      </CardFailed>
    );
  }

  // 🚨 **그 밖의 거절(4xx)은 다시 시도가 없다** — 형식 거절(2000자 초과) · 권한 없음 같은 것이라 같은 본문은
  //    몇 번을 보내도 같은 답이다 (`canRetry` · #231 리뷰). 서버 문구를 그대로 쓰고 고쳐 쓰기만 둔다.
  if (isRejected(error)) {
    return (
      <CardFailed>
        <p>{isApiError(error) ? error.message : "보내지 못했어요."}</p>
        <div className="mt-3">
          <RewriteButton blocked={rewriteBlocked} onClick={rewrite} />
        </div>
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
      <div className="mt-3 flex flex-wrap items-start gap-2">
        {/* 같은 키로 나가는 재시도라 실제로 두 번 저장되지 않는다 (lib/api/idempotency.ts). */}
        <Button variant="secondary" size="compact" onClick={() => retryTurn(childId, turn.id)}>
          다시 시도
        </Button>
        <RewriteButton blocked={rewriteBlocked} onClick={rewrite} />
      </div>
    </CardFailed>
  );
}
