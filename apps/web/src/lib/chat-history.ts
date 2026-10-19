import type { InfiniteData, QueryClient, QueryKey } from "@tanstack/react-query";

import { initialRunState, runReducer, type RunState } from "@/hooks/use-run-stream";
import { api } from "@/lib/api/client";
import { qk } from "@/lib/api/queryKeys";
import type { RunEvent } from "@/lib/api/sse";
import type { ChatDayResponse, ChatReply, ChatTurnOut, StoredRef } from "@/lib/api/types";

/**
 * 04 지난 대화 (#228) — 서버가 남긴 턴을 대화 화면이 그리는 모양으로 옮긴다.
 *
 * 🚨 **답 묶음을 새로 만들지 않는다.** 서버가 남긴 답은 run 화면(SSE)과 같은 이름 · 같은 칸이라, `type` 을
 *    떼어 `runReducer` 에 흘리면 그날 화면에서 본 `RunState` 가 그대로 나온다. 지난 턴용 화면을 따로 두면
 *    같은 답이 오늘과 어제 다르게 그려지고, 리듀서의 규칙(`partial` 뒤 `done` 은 부분 결과 등)을 두 곳에서 지켜야 한다.
 * 🚨 **이 길로 흘린 것은 저장소에 아무것도 얹지 않는다.** 실시간 run 은 일정 초안이 도착하는 순간 초안 보관함에
 *    얹는데(`stores/conversation.ts` 의 `seedDrafts`), 지난 턴을 거기 얹으면 이미 넣은 일정이 초안으로 되살아난다.
 *    그래서 리듀서만 부르고 대화 스토어의 `dispatch` 를 거치지 않는다.
 */

/** 서버에서 받은 지난 턴 하나. 🚨 대화 스토어의 `ChatTurn` 이 아니다 — 보낼 키도 다시 시도도 없다. */
export interface PastTurn {
  runId: string;
  /** 서버가 정한 대화 날짜(응답의 `date`). 🚨 화면이 `created_at` 으로 날짜를 계산하지 않는다. */
  day: string;
  /** 보낸 글 그대로. */
  text: string;
  replyTo: string | null;
  /** 이 턴의 되묻기에 아직 답할 수 있는가 (받은 순간의 값 · 서버 `reply_open`). */
  replyOpen: boolean;
  run: RunState;
  /** 그 run 에서 저장했지만 보호자가 지운 기록. 내용 없이 id 만 있다. */
  deleted: StoredRef[];
}

/** 다음 장을 부르는 값. 더 이른 쪽은 같은 날 안의 커서 또는 전날, 더 늦은 쪽은 다음 날이다. */
export type ChatPageParam = { cursor: string } | { date: string };

/** 지난 대화 한 장의 주소. 첫 장(`null`)은 날짜 없이 부른다 — 서버의 오늘이다. */
export function chatQuery(param: ChatPageParam | null): Record<string, string> {
  if (param === null) return {};
  // 🚨 커서만 보낸다. 커서가 날짜를 품고 있고, `date` 를 같이 보내 어긋나면 400 이다.
  return "cursor" in param ? { cursor: param.cursor } : { date: param.date };
}

/**
 * 그 장 다음에 부를 장. 🚨 **그날 안의 커서가 먼저다** — 그날을 다 받기 전에 전날로 넘어가면 그날의 앞부분이
 * 빠진 채로 날짜 구분선이 선다. 지금은 하루 입력 한도가 `limit` 보다 작아서 커서가 거의 없지만, 한도가 늘어도
 * 계약이 바뀌지 않게 서버가 처음부터 두었다.
 */
export function olderPageParam(page: ChatDayResponse): ChatPageParam | undefined {
  if (page.next_cursor !== null) return { cursor: page.next_cursor };
  if (page.prev_date !== null) return { date: page.prev_date };
  return undefined;
}

/**
 * 맨 위(가장 늦은) 장 다음에 부를 더 늦은 장. 지난 날을 바로 열었을 때(09 입구)만 쓰인다 — 오늘부터 연
 * 목록은 첫 장이 오늘이라 `next_date` 가 늘 `null` 이다.
 * ⚠️ 다음 날이 여러 장이면(하루 20턴 초과) 그날의 앞부분이 빠진다 — 서버가 최근 턴부터 끊어 준다. 지금 하루
 *    입력 한도(5회)로는 하루가 늘 한 장이라 생기지 않는다. 한도가 `limit` 를 넘게 늘면 여기를 다시 본다.
 */
export function newerPageParam(page: ChatDayResponse): ChatPageParam | undefined {
  return page.next_date !== null ? { date: page.next_date } : undefined;
}

/** 서버가 남긴 답 묶음을 run 화면의 상태로. */
export function toPastRun(replies: ChatReply[]): { run: RunState; deleted: StoredRef[] } {
  let run = initialRunState;
  const deleted: StoredRef[] = [];
  for (const { type, ...data } of replies) {
    if (type === "saved") deleted.push(...(data as { deleted: StoredRef[] }).deleted);
    run = runReducer(run, { type: "event", event: { type, data } as RunEvent });
  }
  // 서버는 늘 `done` 으로 끝낸다. 그래도 빠졌으면 끝난 run 으로 둔다 — 진행 중 카드가 지난 대화에서 영원히 돌면 안 된다.
  if (run.status === "idle") {
    run = runReducer(run, { type: "event", event: { type: "done", data: {} } as RunEvent });
  }
  return { run, deleted };
}

function toPastTurn(turn: ChatTurnOut, day: string): PastTurn {
  return {
    runId: turn.run_id,
    day,
    text: turn.raw_text,
    replyTo: turn.reply_to,
    replyOpen: turn.reply_open,
    ...toPastRun(turn.replies),
  };
}

/**
 * 받아 온 장들을 오래된 순서의 한 줄로. 장은 오늘부터 거꾸로 쌓여 있다(첫 장이 가장 최근).
 * 🚨 같은 날이 두 장에 나뉘어 와도(커서) 뒤 장이 그날의 앞부분이다 — 장 순서를 뒤집기만 하면 된다.
 */
export function pastTurnsOf(pages: readonly ChatDayResponse[]): PastTurn[] {
  return [...pages].reverse().flatMap((page) => page.turns.map((t) => toPastTurn(t, page.date)));
}

/* ── run 이 끝난 뒤 ───────────────────────────────────────────────────── */

export type ChatPages = InfiniteData<ChatDayResponse, ChatPageParam | null>;

/**
 * 오늘 장만 갈아 끼운 대화 목록. 갈아 끼우면 안 되는 경우는 `null` 이다 — 그때는 목록을 통째로 다시 받는다.
 *
 * - 자정을 넘겼다(받은 첫 장의 날이 다르다) — 어제 장을 오늘 장으로 덮으면 어제가 사라진다
 * - 오늘이 여러 장이다(`next_cursor`) — 첫 장의 창이 밀려서, 뒤 장과 겹치거나 사이가 빈다
 * - 전날이 바뀌었다(`prev_date`) — 뒤에 붙인 장이 그 값으로 받은 것이라 어긋난다
 */
export function withNewestPage(data: ChatPages, newest: ChatDayResponse): ChatPages | null {
  const current = data.pages[0];
  if (!current || current.date !== newest.date) return null;
  if (current.next_cursor !== null || newest.next_cursor !== null) return null;
  if (current.prev_date !== newest.prev_date) return null;
  return { ...data, pages: [newest, ...data.pages.slice(1)] };
}

/** 대화 목록인가 (어느 날부터 열었든). `qk.chatToday` 처럼 같은 접두어 아래의 다른 키는 아니다. */
function isChatList(key: QueryKey, childId: string): boolean {
  const prefix = [...qk.chat(childId), "list"];
  return key.length > prefix.length && prefix.every((part, i) => key[i] === part);
}

/**
 * run 이 끝난 뒤의 무효화 (#228 리뷰). 🚨 **대화 목록은 통째로 다시 받지 않고 오늘 장만 갈아 끼운다.**
 *
 * run 하나가 홈 · 관찰 · 프로필을 같이 바꿔서 `qk.child` 는 통째로 무효화한다(`docs/web/chat-run-v1.md` §3).
 * 그런데 대화 목록은 무한 목록이라 무효화하면 받아 둔 장을 전부 순서대로 다시 받는다 — 지난 날을 멀리 올려 둔
 * 화면에서 한 줄 보낼 때마다 그만큼 요청이 나간다. run 이 바꾸는 것은 오늘 장 하나뿐이다.
 */
export async function refreshAfterRun(queryClient: QueryClient, childId: string): Promise<void> {
  void queryClient.invalidateQueries({
    queryKey: qk.child(childId),
    predicate: (query) => !isChatList(query.queryKey, childId),
  });
  await refreshTodayChat(queryClient, childId);
}

/**
 * 대화 목록의 오늘 장만 다시 받는다. run 이 끝났을 때, 그리고 지난 질문의 맥락이 사라졌다고 서버가 말했을 때
 * (`reply_context_unavailable` — 다시 받으면 그 턴의 `reply_open` 이 닫혀 온다) 부른다.
 *
 * - 오늘부터 연 목록(`qk.chatList(cid, null)`)을 받아 둔 적이 없으면 할 일이 없다 — 열 때 처음부터 받는다
 * - 지난 날부터 연 목록(09 입구)은 낡았다고만 표시한다 — 오늘 장이 붙어 있는지 모르고, 그 화면에서 보내면
 *   오늘부터 연 화면으로 넘어간다 (`chat/page.tsx`)
 * - 오늘 장을 못 받으면 낡았다고만 표시한다 — 다음에 대화 화면을 열 때 다시 받는다
 * - ⚠️ 그 사이 더 이른 장을 받는 중이었으면 그 결과가 이 갈아 끼운 것을 덮을 수 있다. 그래도 방금 run 은
 *   대화 스토어에 있어서 화면에서 빠지지 않고, 다음 run 이 끝나면 다시 맞춰진다
 */
export async function refreshTodayChat(queryClient: QueryClient, childId: string): Promise<void> {
  const todayList = qk.chatList(childId, null);
  void queryClient.invalidateQueries({
    queryKey: [...qk.chat(childId), "list"],
    predicate: (query) => query.queryKey.at(-1) !== null,
    refetchType: "none",
  });
  void queryClient.invalidateQueries({ queryKey: qk.chatToday(childId) });
  if (!queryClient.getQueryData<ChatPages>(todayList)) return;

  let newest: ChatDayResponse;
  try {
    newest = await api.get<ChatDayResponse>(`/children/${childId}/chat`);
  } catch {
    void queryClient.invalidateQueries({ queryKey: todayList, exact: true, refetchType: "none" });
    return;
  }
  // 기다리는 동안 바뀌었을 수 있다(더 이른 장이 붙었다) — 지금 것에 끼운다.
  const data = queryClient.getQueryData<ChatPages>(todayList);
  if (!data) return;
  const next = withNewestPage(data, newest);
  if (next) queryClient.setQueryData<ChatPages>(todayList, next);
  else void queryClient.invalidateQueries({ queryKey: todayList, exact: true });
}
