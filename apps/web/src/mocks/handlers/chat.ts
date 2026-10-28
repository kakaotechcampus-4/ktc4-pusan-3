import { http, HttpResponse } from "msw";

import type { ChatDayResponse, ChatReply, ChatTurnOut } from "@/lib/api/types";

import { daysAgo, healthObservation, observations, runEventDrafts, suggestions } from "../fixtures";
import { currentScenario } from "../scenario";
import { apiError, networkDelay, url } from "./helpers";
import { pendingReplies } from "./pending-replies";

/**
 * 04 지난 대화 — 서버 `GET /children/{cid}/chat` (#228 · PR #498) 와 같은 모양을 내린다.
 *
 * - 🚨 **목에서 끝난 run 을 그대로 남긴다** (`recordingTurn`). 그래야 대화 화면이 서버 턴과 이 앱의 줄을
 *   `run_id` 로 합치는지(같은 말풍선이 두 번 서지 않는지) 기본 시나리오에서도 보인다. 서버처럼 `done` 으로 끝난
 *   run 만 남기고, `failed` · 끝 신호 없이 끊긴 run 은 남기지 않는다.
 * - 🚨 **지난 날의 대화는 `chat_history` 시나리오만 낸다.** 기본 시나리오에 지난 대화를 깔면 04 를 여는 기존
 *   e2e 가 모두 그 줄들을 같이 보게 된다. 새로고침하면 목 상태가 비는 것이 "앱을 다시 켠 것" 이라, 그때
 *   오늘 대화가 다시 그려지는지도 이 시나리오의 오늘 턴으로 본다.
 * - 대화는 쓴 보호자 본인 것만 나온다 — 목에는 보호자가 하나라 거르지 않는다.
 * - `reply_open` 은 저장하지 않고 조회할 때 정한다 — 서버처럼 되묻기 맥락(`pending-replies.ts`)이 아직 있는가다.
 */

interface StoredTurn {
  childId: string;
  /** 서버가 정한 대화 날짜. 되묻기 답은 질문한 날이다. */
  day: string;
  turn: Omit<ChatTurnOut, "reply_open">;
}

/** 이 목에서 끝난 run. 🚨 프로세스 수명만큼 산다 — 테스트는 `resetChatState()` 로 비운다. */
const recorded: StoredTurn[] = [];

/** `chat_history` 의 열린 질문을 되묻기 맥락에 한 번 넣었는가. 답하면 맥락이 빠지는데, 다시 넣으면 또 열린다. */
let seededOpenQuestion = false;

/**
 * 남겼지만 아직 지난 대화에 내지 않는 run (#538 · `replay_finished`). 서버는 채널을 닫은 **뒤에** 대화를 쓴다 — 화면이
 * 끝을 못 받고 오늘 대화를 다시 받는 그 순간에는 아직 없을 수 있다. 목은 다음 입력이 올 때까지 숨긴다(`revealTurns`).
 * 안 숨기면 결과 미확인 카드가 서자마자 저장된 결과로 바뀌어서, 그 카드에서 다시 보내는 길을 볼 수 없다.
 */
const hiddenRuns = new Set<string>();

export function resetChatState(): void {
  recorded.length = 0;
  seededOpenQuestion = false;
  hiddenRuns.clear();
}

export function hideTurn(runId: string): void {
  hiddenRuns.add(runId);
}

/** 다음 입력이 왔다 — 그새 서버가 대화를 다 썼다. */
export function revealTurns(): void {
  hiddenRuns.clear();
}

const CURSOR_SEPARATOR = "~";

/* ── run 을 대화로 남기기 ─────────────────────────────────────────────── */

/** SSE 한 프레임을 다시 읽는다 (`runs.ts` 의 `frame` 과 짝). */
function readFrame(chunk: Uint8Array): { event: string; data: Record<string, unknown> } | null {
  const text = new TextDecoder().decode(chunk);
  const event = /^event: (.+)$/m.exec(text)?.[1];
  const data = /^data: (.+)$/m.exec(text)?.[1];
  if (!event || data === undefined) return null;
  return { event, data: JSON.parse(data) as Record<string, unknown> };
}

/**
 * 화면 이벤트를 서버가 남기는 모양으로. 서버는 번역 전 이벤트에서 모으지만 결과는 같은 이름 · 같은 칸이다.
 * 🚨 남기지 않는 것 — `step` · `ping` · `offer` · `promoted`(서버에 그런 run 이벤트가 없다) · `failed`.
 */
function toReply(event: string, data: Record<string, unknown>): ChatReply | null {
  switch (event) {
    case "note":
      return {
        type: "note",
        text: String(data.text),
        kind: data.kind === "question" ? "question" : "message",
      };
    case "saved":
      return {
        type: "saved",
        ...(data as Omit<Extract<ChatReply, { type: "saved" }>, "type">),
        deleted: [],
      };
    case "event_draft":
    case "event_drafts":
      return {
        type: "event_draft",
        drafts: data.drafts as Extract<ChatReply, { type: "event_draft" }>["drafts"],
      };
    case "agent_result": {
      // 서버는 조회할 때 추천의 지금 상태를 채운다. 목의 추천은 고르기 전이라 draft 다.
      const ids = (data.suggestion_ids as string[] | undefined) ?? [];
      return {
        type: "agent_result",
        ...(data as Omit<Extract<ChatReply, { type: "agent_result" }>, "type" | "suggestions">),
        suggestions: ids.map((id) => ({ id, status: "draft" as const })),
      };
    }
    case "guidance":
    case "unavailable":
    case "partial":
      return { type: event, ...data } as ChatReply;
    default:
      return null;
  }
}

/**
 * 입력 run 의 대본을 흘리면서 대화 한 턴으로 모은다. 🚨 **`done` 을 내보내기 직전에 남긴다** — 서버는 채널을
 * 닫은 뒤에 쓰지만, 목에서까지 늦게 쓰면 화면의 무효화가 그 턴을 놓치는 경우가 테스트마다 갈린다.
 */
export async function* recordingTurn(
  runId: string,
  input: { childId: string; text: string; replyTo?: string } | undefined,
  script: AsyncGenerator<Uint8Array>,
): AsyncGenerator<Uint8Array> {
  const replies: ChatReply[] = [];
  for await (const chunk of script) {
    const frame = readFrame(chunk);
    if (frame && input) {
      // 🚨 run 하나에 한 번만 남긴다 (서버는 run 당 한 행 · `run_id` UNIQUE). 같은 키로 다시 시도해 같은 run 을
      //    다시 구독하면 대본이 처음부터 또 흐른다.
      if (frame.event === "done" && !recorded.some((t) => t.turn.run_id === runId)) {
        replies.push({ type: "done", completed: frame.data.completed !== false });
        const askedOn = input.replyTo
          ? recorded.find((t) => t.turn.run_id === input.replyTo)?.day
          : undefined;
        recorded.push({
          childId: input.childId,
          day: askedOn ?? daysAgo(0),
          turn: {
            run_id: runId,
            raw_text: input.text,
            reply_to: input.replyTo ?? null,
            created_at: new Date().toISOString(),
            replies,
          },
        });
      } else if (frame.event !== "done") {
        const reply = toReply(frame.event, frame.data);
        if (reply) replies.push(reply);
      }
    }
    yield chunk;
  }
}

/**
 * 이 목이 대화로 남긴 run 의 날짜. 없으면 `null` — 서버의 `find_turn_date` 와 같은 자리다 (#538).
 * 같은 키 재생에서 채널이 지워진 run 이 끝났는지를 이걸로 가른다 (`runs.ts` 의 `replayInput`).
 */
export function recordedDayOf(runId: string): string | null {
  return recorded.find((t) => t.turn.run_id === runId)?.day ?? null;
}

/* ── chat_history 시나리오의 지난 대화 ─────────────────────────────────── */

const done: ChatReply = { type: "done", completed: true };

/**
 * 오늘 · 어제 · 사흘 전. 🚨 **화면이 지켜야 하는 지난 턴의 경우를 하나씩 담는다** — 지운 기록, 답한 질문,
 * 답하지 않은 질문(닫힘), 아직 답할 수 있는 질문(오늘 · `reply_open`), 지난 일정 초안, 다시 준비할 수 있던
 * 부분 실패(다시 준비하기 없음), 고를 수 있는 추천이 남은 결과(후보 보기가 선다)와 다 지난 결과(안 선다),
 * 건강 기록(건강 모양), 잘못된 기록으로 고친 기록.
 * 🚨 실제 발화가 아니다 (최상위 §9).
 */
function historyTurns(childId: string): StoredTurn[] {
  const at = (day: string, hour: string) => `${day}T${hour}:00+09:00`;
  const today = daysAgo(0);
  const yesterday = daysAgo(1);
  const earlier = daysAgo(3);
  const foodIds = suggestions
    .filter((s) => s.agent === "food")
    .slice(0, 2)
    .map((s) => s.id);
  const playIds = suggestions
    .filter((s) => s.agent === "activity")
    .slice(0, 1)
    .map((s) => s.id);
  return [
    {
      childId,
      day: earlier,
      turn: {
        run_id: "r_hist_dinner",
        raw_text: "저녁 뭐 먹일지 같이 정해 줘",
        reply_to: null,
        created_at: at(earlier, "17:10"),
        replies: [
          {
            type: "agent_result",
            agent: "food",
            task_type: "meal_recommendation",
            status: "completed",
            readouts: [],
            question: null,
            suggestion_ids: foodIds,
            kind: "personalized",
            reason: "아이 기록 4건을 보고 골랐어요.",
            record_count: null,
            // 하나는 채택했다 — 05 에서 보이니 "후보 보기" 가 선다.
            suggestions: [
              { id: foodIds[0], status: "approved" },
              { id: foodIds[1], status: "expired" },
            ],
          },
          {
            type: "partial",
            reason: "timeout_20s",
            succeeded: ["food"],
            failed: ["activity"],
            retryable: ["activity"],
          },
          done,
        ],
      },
    },
    {
      childId,
      day: earlier,
      turn: {
        run_id: "r_hist_blocks",
        raw_text: "블록 놀이를 오래 했어",
        reply_to: null,
        created_at: at(earlier, "19:40"),
        replies: [
          // 그날 저장했지만 보호자가 지운 기록 — 내용 없이 id 만 온다.
          {
            type: "saved",
            observations: [],
            deleted: [{ kind: "observation_activity", id: "o_hist_deleted" }],
          },
          { type: "note", text: "혼자 놀았는지 같이 놀았는지 알려주시겠어요?", kind: "question" },
          done,
        ],
      },
    },
    {
      childId,
      day: earlier,
      turn: {
        run_id: "r_hist_play",
        raw_text: "주말에 뭐 하고 놀지 골라 줘",
        reply_to: null,
        created_at: at(earlier, "20:30"),
        replies: [
          {
            type: "agent_result",
            agent: "activity",
            task_type: null,
            status: "completed",
            readouts: [],
            question: null,
            suggestion_ids: playIds,
            kind: "personalized",
            reason: "아이 기록 3건을 보고 골랐어요.",
            record_count: null,
            // 고르지 않은 채 24시간이 지났다 — 고를 수 있는 것이 없어 "후보 보기" 가 서지 않는다.
            suggestions: playIds.map((id) => ({ id, status: "expired" as const })),
          },
          done,
        ],
      },
    },
    {
      childId,
      day: yesterday,
      turn: {
        run_id: "r_hist_cough",
        raw_text: "요즘 기침을 해",
        reply_to: null,
        created_at: at(yesterday, "08:20"),
        replies: [
          { type: "note", text: "언제부터 그랬는지 알려주시겠어요?", kind: "question" },
          done,
        ],
      },
    },
    {
      childId,
      day: yesterday,
      turn: {
        run_id: "r_hist_cough_reply",
        raw_text: "사흘 전부터",
        reply_to: "r_hist_cough",
        created_at: at(yesterday, "08:25"),
        replies: [
          { type: "saved", observations: [healthObservation], deleted: [] },
          { type: "note", text: "말씀해 주신 시점까지 함께 기록해 두었어요.", kind: "message" },
          done,
        ],
      },
    },
    {
      childId,
      day: yesterday,
      turn: {
        run_id: "r_hist_pool",
        raw_text: "주말에 물놀이 가기로 했어",
        reply_to: null,
        created_at: at(yesterday, "21:05"),
        replies: [{ type: "event_draft", drafts: runEventDrafts.slice(0, 1) }, done],
      },
    },
    {
      childId,
      day: today,
      turn: {
        run_id: "r_hist_breakfast",
        raw_text: "아침에 계란을 잘 먹었어",
        reply_to: null,
        created_at: at(today, "07:50"),
        replies: [
          {
            type: "saved",
            // 하나는 보호자가 "잘못된 기록" 으로 고쳤다 — 지우지 않고 상태만 바뀌어서 지금 모습으로 온다.
            observations: [observations[0], { ...observations[1], status: "inactive" }],
            deleted: [],
          },
          done,
        ],
      },
    },
    {
      childId,
      day: today,
      turn: {
        run_id: OPEN_QUESTION_RUN,
        raw_text: "낮잠을 짧게 잤어",
        reply_to: null,
        created_at: at(today, "13:10"),
        // 방금 물었다 — 앱을 다시 켜도 15분 안이라 이어서 답할 수 있다 (`reply_open`).
        replies: [{ type: "note", text: "몇 분쯤 잤는지 알려주시겠어요?", kind: "question" }, done],
      },
    },
  ];
}

/** `chat_history` 에서 아직 답할 수 있는 질문. */
const OPEN_QUESTION_RUN = "r_hist_nap";

function turnsOf(childId: string): StoredTurn[] {
  const scenario = currentScenario();
  if (scenario === "empty") return [];
  if (scenario === "chat_history" && !seededOpenQuestion) {
    // 서버처럼 되묻기 맥락이 있어야 답이 400 이 아니다. 한 번만 넣는다 — 답하면 맥락이 빠진다.
    seededOpenQuestion = true;
    pendingReplies.set(OPEN_QUESTION_RUN, childId);
  }
  const base =
    scenario === "chat_history"
      ? historyTurns(childId)
      : // 오늘 대화가 없는 날 — 캘린더에서 지난 날로 들어올 때 첫 장이 비어 온다.
        scenario === "chat_history_past"
        ? historyTurns(childId).filter((t) => t.day !== daysAgo(0))
        : [];
  return [
    ...base,
    ...recorded.filter((t) => t.childId === childId && !hiddenRuns.has(t.turn.run_id)),
  ];
}

/** 09 하루 보기의 `chat_count`. 🚨 같은 표에서 센다 — 어긋나면 "대화 2개" 를 눌렀는데 1개가 나온다. */
export function chatCountOn(childId: string, date: string): number {
  return turnsOf(childId).filter((t) => t.day === date).length;
}

function isDateKey(value: string): boolean {
  return /^\d{4}-\d{2}-\d{2}$/.test(value);
}

export const chatHandlers = [
  http.get(url("/children/:cid/chat"), async ({ params, request }) => {
    await networkDelay();
    const childId = String(params.cid);
    const query = new URL(request.url).searchParams;
    const dateParam = query.get("date");
    const cursor = query.get("cursor");
    const limit = Number(query.get("limit") ?? 20);
    if (!Number.isInteger(limit) || limit < 1 || limit > 100) {
      return apiError(400, "validation_failed", "요청 형식이 올바르지 않아요");
    }

    // 🚨 커서는 자기 날짜를 품는다. `date` 와 같이 와서 다르면 400 이다 (서버와 같다).
    let day = dateParam ?? daysAgo(0);
    let before: number | null = null;
    if (cursor !== null) {
      const [cursorDay, index] = cursor.split(CURSOR_SEPARATOR);
      if (!cursorDay || !isDateKey(cursorDay) || !/^\d+$/.test(index ?? "")) {
        return apiError(400, "validation_failed", "목록 위치가 올바르지 않아요");
      }
      if (dateParam !== null && dateParam !== cursorDay) {
        return apiError(400, "validation_failed", "목록 위치가 올바르지 않아요");
      }
      day = cursorDay;
      before = Number(index);
    }
    if (!isDateKey(day)) return apiError(400, "validation_failed", "날짜가 올바르지 않아요");

    const all = turnsOf(childId);
    const ofDay = all
      .filter((t) => t.day === day)
      .map((t) => t.turn)
      .sort((a, b) => a.created_at.localeCompare(b.created_at));
    // 최근 것부터 limit 개를 끊고, 안은 오래된 순이다.
    const end = before ?? ofDay.length;
    const start = Math.max(0, end - limit);
    const days = [...new Set(all.map((t) => t.day))].sort();

    const body: ChatDayResponse = {
      date: day,
      turns: ofDay.slice(start, end).map((turn) => ({
        ...turn,
        // 그 run 의 되묻기에 아직 답할 수 있는가 — 맥락이 남아 있고 그 아이 것이다.
        reply_open:
          turn.replies.some((r) => r.type === "note" && r.kind === "question") &&
          pendingReplies.get(turn.run_id) === childId,
      })),
      next_cursor: start > 0 ? `${day}${CURSOR_SEPARATOR}${start}` : null,
      prev_date: days.filter((d) => d < day).at(-1) ?? null,
      next_date: days.find((d) => d > day) ?? null,
    };
    return HttpResponse.json(body);
  }),
];
