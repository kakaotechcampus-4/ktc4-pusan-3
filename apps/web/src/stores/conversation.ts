import { create } from "zustand";

import {
  initialRunState,
  isRunConfirmed,
  runReducer,
  subscribeRun,
  type RunAction,
  type RunState,
} from "@/hooks/use-run-stream";
import { isApiError } from "@/lib/api/errors";
import {
  createIdempotencyKeyHolder,
  type IdempotencyKey,
  type IdempotencyKeyHolder,
} from "@/lib/api/idempotency";
import { submitInput, type InputRequest } from "@/lib/api/operations";
import { qk } from "@/lib/api/queryKeys";
import { isDraftEvent, type EventDraftsEvent } from "@/lib/api/sse";
import { toSeoulDateKey } from "@/lib/format";
import { getQueryClient } from "@/lib/query-client";
import { useDraftStore } from "@/stores/draft";
import { useEventDraftStore } from "@/stores/event-draft";

/**
 * 04 대화 — 그날 보낸 한 줄 · 그 한 줄의 run · 답을 기다리는 질문. 아이별로 하나 (#226).
 *
 * 03 홈과 대화 화면(`/child/{id}/chat`)이 **같이 읽는다.** 홈에서 보내면 말풍선이 먼저 서고
 * 대화 화면으로 넘어가는데, 요청과 스트림이 화면에 붙어 있으면 그 이동에서 끊긴다. 그래서 보내기 ·
 * 구독 · 끝 처리가 전부 화면 밖(이 파일)에 있고, 화면은 그리기만 한다.
 *
 * ## 이 파일이 옮겨 받은 규칙 (`stores/pending-question.ts` 에서)
 *
 * 1. 🚨 **질문은 문자열이 아니라 `{ text, runId }` 다** (#158 리뷰). 답은 원문을 다시 보내지 않고
 *    `reply_to` 로 **어느 run 에 대한 답인지**만 싣는다. "계란 잘 먹었어. 요즘 기침해" 처럼 일부는
 *    저장되고 질문이 같이 오는 run 이 있어서(`apps/api/app/agents/pipeline.py`), 원문을 다시 보내면
 *    계란이 두 번 저장되고 7일 승격 집계가 한 번의 관찰을 두 번으로 센다 (최상위 §2 · #154).
 * 2. 🚨 **`persist` 를 붙이지 말 것.** 보낸 원문과 되묻는 질문에는 아이 이야기가 그대로 들어 있다.
 *    디스크에 남기지 않는다 (최상위 §2 개인정보 · `stores/draft.ts` 와 같은 규칙). 그래서 앱을 끄면
 *    그날 대화가 사라진다 — 남기는 것은 서버 쪽 일이다 (#228). 🚨 콘솔·로그에도 찍지 않는다.
 * 3. 🚨 **지우는 때는 보낸 때가 아니라 서버가 끝까지 처리했다고 말한 때다** (`isRunConfirmed` ·
 *    #71 · #175 리뷰). 이어받기 run 이 실패하면 서버는 맥락을 되돌려 두고, 화면은 같은 키 ·
 *    같은 `reply_to` 로 다시 보낼 수 있어야 한다. `unconfirmed` 에서 정리하면 저장됐는지 모르는
 *    한 줄의 키가 갈려서 다시 시도가 두 번 저장된다.
 * 4. 🚨 **답을 강요하지 않는다.** 질문은 입력창의 기본 대상이 될 뿐이고 "답하지 않고 새로 적기" 로
 *    내려놓을 수 있다. 내려놓아도 질문은 대화에 **열린 채로** 남아 다시 골라 답할 수 있다 —
 *    예전 "나중에 할게요" 는 질문과 `runId` 를 지워서 실제로는 그만두기였다 (#226). 답하려면 한 줄을
 *    새로 보내야 해서 하루 입력 횟수(#147)를 한 번 더 쓴다.
 *
 * ## 원문은 어디에 있나
 *
 * 보내기 전에는 `stores/draft.ts`(입력창), 보낸 뒤에는 **그 한 줄의 말풍선**(`ChatTurn.body.text`)이다.
 * 둘 다 메모리 전용이고 로그아웃에서 지운다. 🚨 말풍선은 서버가 끝을 말하기 전에 사라지지 않는다 —
 * 실패하면 그 자리에서 다시 시도하거나, "고쳐 쓰기" 로 입력창에 되돌린다.
 *
 * ## 한 번에 하나
 *
 * 🚨 **답을 기다리는 동안에는 다음 한 줄을 보내지 않는다** (`isBusy`). 두 run 이 겹치면 어느 쪽이
 *    질문의 답인지, 질문을 언제 닫는지가 도착 순서에 달린다. Claude · ChatGPT 와 같은 방식이다.
 */

/** Memory 가 되물은 질문 하나. 🚨 `runId` 가 본체다 — 위 규칙 1. */
export interface ChatQuestion {
  /** 화면에 그대로 그린다. 🚨 LLM 이 만든 문장이라 HTML 로 그리지 않는다 (apps/web/CLAUDE.md §4). */
  text: string;
  /** 그 질문을 낸 run. 답을 보낼 때 `reply_to` 로 싣는다. */
  runId: string;
}

/**
 * 보내기의 상태. run 이 시작되기 **전**이다 — 202 를 받았는지가 여기서 갈린다.
 * 🚨 `error` 는 202 를 못 받은 것이다. 거절(400 · 429 · 403)이거나, 네트워크 · 5xx 로 응답을 못 받은
 *    것이다 — 뒤의 경우는 서버가 받았을 수 있어서 다시 시도가 **같은 키**로 나간다.
 */
export type SendState =
  { status: "sending" } | { status: "sent"; runId: string } | { status: "error"; error: unknown };

/** 이 run 이 낸 질문이 지금 어떤가. 질문을 안 낸 run 은 `null`. */
export type QuestionState = "open" | "answered" | "dropped";

export interface ChatTurn {
  /** 화면 key. 서버 id 가 아니다. */
  id: string;
  /**
   * 이 한 줄이 속한 날 (한국 시간 `YYYY-MM-DD`). 🚨 **보낸 순간에 정한다** — 자정을 넘겨 도착한
   * 결과도 이 날에 붙는다. 답은 **질문이 나온 날**을 물려받는다 (#226).
   */
  day: string;
  /** 보낸 본문 그대로. 🚨 다시 시도는 이 값을 그대로 보낸다 — 그때의 입력창이 아니다. */
  body: InputRequest;
  /** 이 한 줄이 답한 질문. 400 때 무엇을 물었는지 말풍선 아래에 다시 보여준다. */
  answering: ChatQuestion | null;
  /** 🚨 보낸 순간의 키를 붙들어 둔다. 다시 시도가 같은 키로 나가야 두 번 저장되지 않는다. */
  key: IdempotencyKey;
  send: SendState;
  run: RunState;
  question: QuestionState | null;
  /** "고쳐 쓰기" 로 입력창에 넘긴 뒤 — 같은 자리의 다시 시도 버튼을 내린다. */
  rewritten: boolean;
}

interface ChildConversation {
  turns: ChatTurn[];
  /** 입력창이 지금 답하는 질문. `null` 이면 다음 한 줄은 새 이야기다. */
  answering: ChatQuestion | null;
}

const EMPTY: ChildConversation = { turns: [], answering: null };

interface ConversationState {
  byChild: Record<string, ChildConversation>;
  /** 로그아웃에서 부른다 — 스트림도 같이 끊는다. 다음 사람에게 남의 아이 이야기를 넘기지 않는다. */
  clearAll: () => void;
}

export const useConversationStore = create<ConversationState>()((set) => ({
  byChild: {},
  clearAll: () => {
    for (const unsubscribe of streams.values()) unsubscribe();
    streams.clear();
    keyHolders.clear();
    seededDrafts.clear();
    set({ byChild: {} });
  },
}));

/* ── 화면 밖에 사는 것들 ──────────────────────────────────────────────── */

/** turnId → 구독 끊기. 🚨 화면 언마운트로 끊지 않는다 — 로그아웃 · 다시 시도에서만 끊는다. */
const streams = new Map<string, () => void>();

/**
 * childId → 한 줄 입력의 키. 🚨 **본문에 묶는다** (`current(keyPayload(body))` · PR #71 리뷰).
 * 고쳐 쓴 본문은 새 키, 그대로 다시 보낸 본문은 같은 키다. 서버가 끝을 말했을 때만 돌린다.
 */
const keyHolders = new Map<string, IdempotencyKeyHolder>();

/** 일정 초안을 `stores/event-draft.ts` 에 얹은 적이 있는 `draft_id`. 아래 `seedDrafts` 참고. */
const seededDrafts = new Set<string>();

function keyHolder(childId: string): IdempotencyKeyHolder {
  let holder = keyHolders.get(childId);
  if (!holder) {
    holder = createIdempotencyKeyHolder();
    keyHolders.set(childId, holder);
  }
  return holder;
}

let turnSeq = 0;

/* ── 읽기 ─────────────────────────────────────────────────────────────── */

export function conversationOf(state: ConversationState, childId: string): ChildConversation {
  return state.byChild[childId] ?? EMPTY;
}

/** 답을 기다리는 중인가 — 보내는 중이거나 run 이 도는 중. */
export function isBusy(conversation: ChildConversation): boolean {
  const last = conversation.turns.at(-1);
  if (!last) return false;
  return last.send.status === "sending" || last.run.status === "streaming";
}

/** 아직 답할 수 있는 질문이 있는가 — 03 홈 입구 문구가 이걸로 바뀐다. */
export function hasOpenQuestion(conversation: ChildConversation): boolean {
  return conversation.turns.some((turn) => turn.question === "open");
}

/**
 * 서버에 **닿지 못한** 실패인가 — 보내기 실패 · 400 · 429 · 403 (#226 "실패 표시" 표).
 * 🚨 이 말풍선은 다음 한 줄 · 다시 시도 · 고쳐 쓰기가 **대체**한다. run 이 없어서 대화에 남길 답이
 *    없다. `unconfirmed` 는 다르다 — run 이 돌았고 서버가 저장했을 수 있어 그대로 둔다.
 */
export function isUnreached(turn: ChatTurn): boolean {
  return turn.send.status === "error";
}

/**
 * 같은 자리에서 **같은 키로** 다시 보낼 수 있는가.
 * 🚨 429 · 400 · 403 은 아니다 — 같은 키 · 같은 본문이면 몇 번을 보내도 같은 답이다 (#147 · #175).
 */
export function canRetry(turn: ChatTurn): boolean {
  if (turn.rewritten) return false;
  if (turn.send.status === "error") {
    const { error } = turn.send;
    return !(
      isApiError(error, "daily_input_limit") ||
      isApiError(error, "reply_context_unavailable") ||
      isApiError(error, "consent_required")
    );
  }
  return turn.run.status === "failed" || turn.run.status === "unconfirmed";
}

/* ── 쓰기 ─────────────────────────────────────────────────────────────── */

function update(childId: string, fn: (conversation: ChildConversation) => ChildConversation): void {
  useConversationStore.setState((s) => ({
    byChild: { ...s.byChild, [childId]: fn(s.byChild[childId] ?? EMPTY) },
  }));
}

function updateTurn(childId: string, turnId: string, fn: (turn: ChatTurn) => ChatTurn): void {
  update(childId, (c) => ({
    ...c,
    turns: c.turns.map((turn) => (turn.id === turnId ? fn(turn) : turn)),
  }));
}

function findTurn(childId: string, turnId: string): ChatTurn | undefined {
  return conversationOf(useConversationStore.getState(), childId).turns.find(
    (t) => t.id === turnId,
  );
}

/**
 * Idempotency-Key 를 묶을 본문 지문.
 *
 * 🚨 **`text` 만으로는 부족하다.** 요청 지문은 `method + 경로 + 요청 본문` 이라
 *    (`docs/api/idempotency-v1.md` §3-2), "네" 라는 같은 답을 서로 다른 질문에 보내면
 *    같은 키에 다른 본문(`reply_to`)이 실려 `422 idempotency_key_reuse` 가 된다.
 * 🚨 필드를 더하면 **여기도 같이** 더한다 — 빠뜨리면 그 필드만 바뀐 요청이 조용히 같은 키로 나간다.
 */
export function keyPayload(body: InputRequest): string {
  return `${body.reply_to ?? ""}|${body.source}|${body.text}`;
}

/**
 * 한 줄을 보낸다. 말풍선이 **먼저** 서고(202 를 기다리지 않는다) 요청은 뒤에서 나간다.
 *
 * @param asReply 입력창이 답하던 질문에 대한 답으로 보낼지. 🚨 03 홈은 `false` 다 — 질문이 화면에
 *   안 보이는 곳에서 보낸 한 줄을 답으로 묶지 않는다. 대화 화면은 입력창 위에 질문을 띄우고 있을
 *   때만 `true` 를 넘긴다.
 * @returns 만든 말풍선 id. 이미 답을 기다리는 중이면 아무것도 하지 않고 `null`.
 */
export function sendLine(
  childId: string,
  text: string,
  { asReply, source }: { asReply: boolean; source: InputRequest["source"] },
): string | null {
  const conversation = conversationOf(useConversationStore.getState(), childId);
  const trimmed = text.trim();
  if (!trimmed || isBusy(conversation)) return null;

  const answering = asReply ? conversation.answering : null;
  const body: InputRequest = {
    text: trimmed,
    source,
    // 🚨 원문을 다시 보내지 않고 **어느 run 에 대한 답인지**만 싣는다 (위 규칙 1).
    ...(answering ? { reply_to: answering.runId } : {}),
  };
  // 🚨 자정을 넘겨 온 답은 질문이 나온 날에 붙인다 (#226).
  const askedOn = answering
    ? conversation.turns.find((t) => t.send.status === "sent" && t.send.runId === answering.runId)
        ?.day
    : undefined;

  const turn: ChatTurn = {
    id: `t${++turnSeq}`,
    day: askedOn ?? toSeoulDateKey(new Date()),
    body,
    answering,
    // 🚨 `newIdempotencyKey()` 가 아니다 — 같은 본문이면 같은 키라야 응답만 잃은 한 줄을 고쳐 쓰지
    //    않고 그대로 다시 보냈을 때 두 번 저장되지 않는다 (`use-idempotency-key.ts`).
    key: keyHolder(childId).current(keyPayload(body)),
    send: { status: "sending" },
    run: initialRunState,
    question: null,
    rewritten: false,
  };

  update(childId, (c) => ({
    ...c,
    // 서버에 닿지 못한 말풍선은 다음 한 줄이 대체한다 (`isUnreached`).
    turns: [...c.turns.filter((t) => !isUnreached(t)), turn],
  }));
  void submit(childId, turn.id);
  return turn.id;
}

/** 같은 자리 · 같은 본문 · **같은 키**로 다시 보낸다. 답하던 질문도 그대로 실린다. */
export function retryTurn(childId: string, turnId: string): void {
  const turn = findTurn(childId, turnId);
  if (!turn || !canRetry(turn)) return;
  if (isBusy(conversationOf(useConversationStore.getState(), childId))) return;
  void submit(childId, turnId);
}

/**
 * 원문을 입력창에 되돌린다 ("고쳐 쓰기").
 *
 * - 서버에 닿지 못한 말풍선은 지운다 — 고쳐 쓴 한 줄이 그 자리를 대체한다.
 * - 🚨 `failed` · `unconfirmed` 는 **남긴다.** 특히 `unconfirmed` 는 서버가 저장했을 수 있어서,
 *   말풍선을 지우면 "저장됐는지 모르는 한 줄" 이 대화에서 사라진다. 다시 시도 버튼만 내린다.
 * - 답하던 질문이 아직 열려 있으면 입력창의 대상으로 되돌린다 — 고쳐 쓴 한 줄도 그 질문의 답이다.
 *
 * 🚨 입력창에 쓰던 글이 있으면 덮지 않는다. 덮으면 그 글이 사라진다.
 */
export function rewriteTurn(childId: string, turnId: string): void {
  const turn = findTurn(childId, turnId);
  if (!turn) return;

  const drafts = useDraftStore.getState();
  if (!(drafts.byChild[childId] ?? "").trim()) drafts.setDraft(childId, turn.body.text);

  update(childId, (c) => {
    const stillOpen =
      turn.answering !== null &&
      c.turns.some(
        (t) =>
          t.question === "open" &&
          t.send.status === "sent" &&
          t.send.runId === turn.answering?.runId,
      );
    return {
      answering: stillOpen ? turn.answering : c.answering,
      turns: isUnreached(turn)
        ? c.turns.filter((t) => t.id !== turnId)
        : c.turns.map((t) => (t.id === turnId ? { ...t, rewritten: true } : t)),
    };
  });
}

/** 열린 질문 하나를 입력창의 대상으로 고른다 (대화 안의 "이 질문에 답하기"). */
export function answerQuestion(childId: string, runId: string): void {
  update(childId, (c) => {
    const asked = c.turns.find(
      (t) => t.question === "open" && t.send.status === "sent" && t.send.runId === runId,
    );
    if (!asked?.run.note) return c;
    return { ...c, answering: { text: asked.run.note.text, runId } };
  });
}

/** 입력창이 답하던 질문을 내려놓는다. 🚨 질문은 대화에 **열린 채로** 남는다 (위 규칙 4). */
export function stopAnswering(childId: string): void {
  update(childId, (c) => (c.answering ? { ...c, answering: null } : c));
}

/* ── 보내기 · 구독 · 끝 ───────────────────────────────────────────────── */

async function submit(childId: string, turnId: string): Promise<void> {
  const turn = findTurn(childId, turnId);
  if (!turn) return;

  streams.get(turnId)?.();
  streams.delete(turnId);
  updateTurn(childId, turnId, (t) => ({ ...t, send: { status: "sending" }, run: initialRunState }));

  let runId: string;
  try {
    ({ run_id: runId } = await submitInput(childId, turn.body, turn.key));
  } catch (error) {
    // 로그아웃으로 대화가 비워졌으면 쓸 자리가 없다.
    if (!findTurn(childId, turnId)) return;
    failSend(childId, turn, error);
    return;
  }

  if (!findTurn(childId, turnId)) return;
  updateTurn(childId, turnId, (t) => ({
    ...t,
    send: { status: "sent", runId },
    run: runReducer(initialRunState, { type: "start" }),
  }));

  const dispatch = (action: RunAction) => {
    if (!findTurn(childId, turnId)) return;
    if (action.type === "event" && isDraftEvent(action.event.type)) {
      seedDrafts(childId, (action.event.data as EventDraftsEvent).drafts);
    }
    updateTurn(childId, turnId, (t) => ({ ...t, run: runReducer(t.run, action) }));
  };

  streams.set(
    turnId,
    subscribeRun(runId, dispatch, () => {
      streams.delete(turnId);
      settle(childId, turnId);
    }),
  );
}

/**
 * 202 를 못 받았다. 🚨 키를 돌리지 않는다 — 실패 뒤 다시 누르는 것이 재시도다.
 *
 * - 400 `reply_context_unavailable` — 🚨 **질문을 놓는다** (#175). 들고 있으면 다음 한 줄에도 같은
 *   `reply_to` 가 실려 몇 번을 보내도 같은 400 이다. 놓으면 본문이 바뀌어 새 키가 저절로 나간다.
 * - 429 · 403 — 🚨 **원문을 입력창에 되돌린다.** 다시 시도가 없어서(같은 키라 같은 답이다) 말풍선에만
 *   두면 다음 한 줄이 그 말풍선을 대체할 때 원문이 사라진다. 429 는 내일 보낼 한 줄이다.
 */
function failSend(childId: string, turn: ChatTurn, error: unknown): void {
  const lostContext = isApiError(error, "reply_context_unavailable");
  const keepInComposer =
    isApiError(error, "daily_input_limit") || isApiError(error, "consent_required");

  if (keepInComposer) {
    const drafts = useDraftStore.getState();
    if (!(drafts.byChild[childId] ?? "").trim()) drafts.setDraft(childId, turn.body.text);
  }

  update(childId, (c) => {
    const droppedRunId = lostContext ? turn.answering?.runId : undefined;
    return {
      answering: droppedRunId && c.answering?.runId === droppedRunId ? null : c.answering,
      turns: c.turns.map((t) => {
        if (t.id === turn.id) return { ...t, send: { status: "error", error } };
        if (droppedRunId && t.send.status === "sent" && t.send.runId === droppedRunId) {
          return { ...t, question: "dropped" };
        }
        return t;
      }),
    };
  });
}

/**
 * 스트림이 끝났다. 🚨 **서버가 끝을 말한 경우에만** 정리한다 (위 규칙 3).
 *
 * 정리하는 것 — 키를 돌린다(다음 한 줄은 새 동작) · 이 한 줄이 답한 질문을 닫는다 ·
 * 이 run 이 또 물었으면 그 질문이 입력창의 새 대상이 된다 (이어받기 run 이 또 물으면 그 run).
 */
function settle(childId: string, turnId: string): void {
  void getQueryClient().invalidateQueries({ queryKey: qk.child(childId) });

  const turn = findTurn(childId, turnId);
  if (!turn || !isRunConfirmed(turn.run.status)) return;

  keyHolder(childId).rotate();
  const answeredRunId = turn.answering?.runId;
  const asked: ChatQuestion | null =
    turn.run.note?.kind === "question" && turn.send.status === "sent"
      ? { text: turn.run.note.text, runId: turn.send.runId }
      : null;

  update(childId, (c) => ({
    answering: asked ?? (c.answering?.runId === answeredRunId ? null : c.answering),
    turns: c.turns.map((t) => {
      if (t.id === turnId) return { ...t, question: asked ? "open" : null };
      if (answeredRunId && t.send.status === "sent" && t.send.runId === answeredRunId) {
        return { ...t, question: "answered" };
      }
      return t;
    }),
  }));
}

/**
 * run 이 낸 일정 초안을 `stores/event-draft.ts` 에 **한 번만** 얹는다.
 *
 * 🚨 대화에서는 초안을 카드 하나로 두고 넣는 것은 시트에서 한다 — 시트는 열 때마다 새로 그려진다.
 *    거기서 얹으면 제출하고 스토어에서 뺀 장이 다시 열 때 되살아나서, 이미 캘린더에 넣은 일정이
 *    초안으로 부활한다 (`EventDraftList` 의 `seen` 과 같은 이유). 그래서 도착하는 순간 여기서 얹는다.
 *    다시 시도로 같은 run 을 처음부터 다시 받아도(서버가 처음부터 다시 보낸다) 두 번 얹지 않는다.
 */
function seedDrafts(childId: string, drafts: EventDraftsEvent["drafts"]): void {
  const fresh = drafts.filter((d) => !seededDrafts.has(d.draft_id));
  if (fresh.length === 0) return;
  for (const d of fresh) seededDrafts.add(d.draft_id);
  useEventDraftStore.getState().addDrafts(childId, "input", fresh);
}

/** 화면에서 읽는 자리. 대화가 없는 아이는 빈 대화로 읽힌다. */
export function useConversation(childId: string): ChildConversation {
  return useConversationStore((s) => conversationOf(s, childId));
}
