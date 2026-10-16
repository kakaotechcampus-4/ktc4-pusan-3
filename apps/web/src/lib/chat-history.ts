import { initialRunState, runReducer, type RunState } from "@/hooks/use-run-stream";
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
  run: RunState;
  /** 그 run 에서 저장했지만 보호자가 지운 기록. 내용 없이 id 만 있다. */
  deleted: StoredRef[];
}

/** 다음(더 이른) 장을 부르는 값. 같은 날 안이면 커서, 그날을 다 받았으면 전날. */
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
