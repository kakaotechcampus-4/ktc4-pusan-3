import { describe, expect, it } from "vitest";

import type { ChatDayResponse, ChatReply } from "@/lib/api/types";
import { healthObservation, observations, runEventDrafts } from "@/mocks/fixtures";
import { useEventDraftStore } from "@/stores/event-draft";

import { chatQuery, olderPageParam, pastTurnsOf, toPastRun } from "./chat-history";

/**
 * 04 지난 대화 (#228) — 서버가 남긴 답을 run 화면과 같은 상태로 되살리는가, 다음 장을 어느 순서로 부르는가.
 * 목이 서버처럼 끝난 run 만 남기는지는 `mocks/contract.test.ts` 가 건다.
 */

const CHILD = "c1";
const done: ChatReply = { type: "done", completed: true };

function page(date: string, overrides: Partial<ChatDayResponse> = {}): ChatDayResponse {
  return { date, turns: [], next_cursor: null, prev_date: null, ...overrides };
}

describe("toPastRun — 리듀서를 그대로 쓴다", () => {
  it("지운 기록은 id 만 따로 모이고, 남은 기록은 지금 모습 그대로다 (건강 모양 포함)", () => {
    const { run, deleted } = toPastRun([
      {
        type: "saved",
        observations: [observations[0], healthObservation],
        deleted: [{ kind: "observation_activity", id: "o_gone" }],
      },
      done,
    ]);
    expect(run.status).toBe("done");
    expect(run.observations).toEqual([observations[0], healthObservation]);
    expect(deleted).toEqual([{ kind: "observation_activity", id: "o_gone" }]);
  });

  it("🚨 partial 뒤의 done 은 그날처럼 부분 결과다 (NF-06)", () => {
    const { run } = toPastRun([
      {
        type: "partial",
        reason: "timeout_20s",
        succeeded: ["food"],
        failed: ["activity"],
        retryable: [],
      },
      done,
    ]);
    expect(run.status).toBe("partial");
    expect(run.partial?.failed).toEqual(["activity"]);
  });

  it("저장한 뒤 끊긴 run 은 그날처럼 끊김으로 남는다 (#271)", () => {
    const { run } = toPastRun([{ type: "done", completed: false }]);
    expect(run.status).toBe("done");
    expect(run.interrupted).toBe(true);
  });

  it("되묻기는 질문으로, 일정 초안은 그때 내용 그대로 되살아난다", () => {
    const { run } = toPastRun([
      { type: "note", text: "언제부터였나요?", kind: "question" },
      { type: "event_draft", drafts: runEventDrafts.slice(0, 1) },
      done,
    ]);
    expect(run.note).toEqual({ text: "언제부터였나요?", kind: "question" });
    expect(run.drafts).toEqual(runEventDrafts.slice(0, 1));
  });

  it("🚨 지난 초안은 초안 보관함에 얹지 않는다 — 넣은 일정이 초안으로 되살아난다", () => {
    const before = useEventDraftStore.getState().byChild[CHILD] ?? [];
    toPastRun([{ type: "event_draft", drafts: runEventDrafts }, done]);
    expect(useEventDraftStore.getState().byChild[CHILD] ?? []).toEqual(before);
  });

  it("done 이 빠져도 진행 중으로 남지 않는다 — 지난 대화에서 진행 카드가 돌면 안 된다", () => {
    const { run } = toPastRun([{ type: "note", text: "기록했어요.", kind: "message" }]);
    expect(run.status).toBe("done");
  });

  it("모르는 답 종류가 와도 버리고 나머지를 그린다 — 서버가 종류를 늘려도 화면을 먼저 배포하지 않는다", () => {
    const { run } = toPastRun([
      { type: "something_new", value: 1 } as unknown as ChatReply,
      { type: "note", text: "기록했어요.", kind: "message" },
      done,
    ]);
    expect(run.note?.text).toBe("기록했어요.");
  });
});

describe("다음 장", () => {
  it("🚨 그날 안의 커서가 전날보다 먼저다 — 그날 앞부분이 빠진 채로 전날로 넘어가지 않는다", () => {
    expect(
      olderPageParam(page("2026-10-16", { next_cursor: "c", prev_date: "2026-10-15" })),
    ).toEqual({
      cursor: "c",
    });
    expect(olderPageParam(page("2026-10-16", { prev_date: "2026-10-15" }))).toEqual({
      date: "2026-10-15",
    });
    expect(olderPageParam(page("2026-10-16"))).toBeUndefined();
  });

  it("🚨 커서만 보낸다 — 날짜를 같이 보내 어긋나면 400 이다", () => {
    expect(chatQuery(null)).toEqual({});
    expect(chatQuery({ cursor: "c" })).toEqual({ cursor: "c" });
    expect(chatQuery({ date: "2026-10-15" })).toEqual({ date: "2026-10-15" });
  });

  it("장은 최근부터 쌓여 있다 — 뒤집어 오래된 순으로 잇고, 날짜는 서버가 준 장의 날짜다", () => {
    const turn = (run_id: string) => ({
      run_id,
      raw_text: run_id,
      reply_to: null,
      created_at: "2026-10-16T00:00:00+09:00",
      replies: [done],
    });
    const turns = pastTurnsOf([
      page("2026-10-16", { turns: [turn("today")] }),
      page("2026-10-15", { turns: [turn("late")] }),
      page("2026-10-15", { turns: [turn("early")] }),
    ]);
    expect(turns.map((t) => [t.runId, t.day])).toEqual([
      ["early", "2026-10-15"],
      ["late", "2026-10-15"],
      ["today", "2026-10-16"],
    ]);
  });
});
