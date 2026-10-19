import { QueryClient } from "@tanstack/react-query";
import { afterEach, describe, expect, it } from "vitest";

import { qk } from "@/lib/api/queryKeys";
import type { ChatDayResponse, ChatReply } from "@/lib/api/types";
import { daysAgo, healthObservation, observations, runEventDrafts } from "@/mocks/fixtures";
import { setScenario } from "@/mocks/scenario";
import { useEventDraftStore } from "@/stores/event-draft";

import {
  chatQuery,
  newerPageParam,
  olderPageParam,
  pastTurnsOf,
  refreshAfterRun,
  toPastRun,
  withNewestPage,
  type ChatPages,
} from "./chat-history";

/**
 * 04 지난 대화 (#228) — 서버가 남긴 답을 run 화면과 같은 상태로 되살리는가, 다음 장을 어느 순서로 부르는가.
 * 목이 서버처럼 끝난 run 만 남기는지는 `mocks/contract.test.ts` 가 건다.
 */

const CHILD = "c1";
const done: ChatReply = { type: "done", completed: true };

function page(date: string, overrides: Partial<ChatDayResponse> = {}): ChatDayResponse {
  return { date, turns: [], next_cursor: null, prev_date: null, next_date: null, ...overrides };
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

  it("더 늦은 장은 다음 날이다 — 지난 날을 바로 열었을 때 아래로 이어 받는다", () => {
    expect(newerPageParam(page("2026-10-13", { next_date: "2026-10-15" }))).toEqual({
      date: "2026-10-15",
    });
    expect(newerPageParam(page("2026-10-16"))).toBeUndefined();
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
      reply_open: run_id === "late",
      replies: [done],
    });
    const turns = pastTurnsOf([
      page("2026-10-16", { turns: [turn("today")] }),
      page("2026-10-15", { turns: [turn("late")] }),
      page("2026-10-15", { turns: [turn("early")] }),
    ]);
    expect(turns.map((t) => [t.runId, t.day, t.replyOpen])).toEqual([
      ["early", "2026-10-15", false],
      ["late", "2026-10-15", true],
      ["today", "2026-10-16", false],
    ]);
  });
});

describe("run 이 끝난 뒤 — 오늘 장만 갈아 끼운다 (#228 리뷰)", () => {
  const older = page("2026-10-15", { prev_date: "2026-10-13" });
  const pages = (first: ChatDayResponse): ChatPages => ({
    pages: [first, older],
    pageParams: [null, { date: "2026-10-15" }],
  });

  it("같은 날이면 첫 장만 바꾸고 받아 둔 지난 장은 그대로 둔다", () => {
    const newest = page("2026-10-16", { prev_date: "2026-10-15" });
    const next = withNewestPage(pages(page("2026-10-16", { prev_date: "2026-10-15" })), newest);
    expect(next?.pages[0]).toBe(newest);
    expect(next?.pages[1]).toBe(older);
    expect(next?.pageParams).toEqual([null, { date: "2026-10-15" }]);
  });

  it("🚨 갈아 끼우면 어긋나는 경우는 통째로 다시 받는다 — 자정, 오늘이 여러 장, 전날이 바뀜", () => {
    const today = page("2026-10-16", { prev_date: "2026-10-15" });
    // 자정을 넘겼다 — 어제 장을 오늘 장으로 덮으면 어제가 사라진다.
    expect(
      withNewestPage(pages(today), page("2026-10-17", { prev_date: "2026-10-16" })),
    ).toBeNull();
    // 오늘이 여러 장 — 첫 장의 창이 밀려 뒤 장과 겹치거나 사이가 빈다.
    expect(withNewestPage(pages(today), { ...today, next_cursor: "c" })).toBeNull();
    expect(withNewestPage(pages({ ...today, next_cursor: "c" }), today)).toBeNull();
    // 전날이 바뀌었다 — 뒤 장이 그 값으로 받은 것이다.
    expect(withNewestPage(pages(today), { ...today, prev_date: "2026-10-14" })).toBeNull();
  });

  describe("refreshAfterRun", () => {
    afterEach(() => setScenario("default"));

    it("오늘 장을 새로 받아 끼우고, 지난 장은 다시 받지 않는다", async () => {
      setScenario("chat_history");
      const client = new QueryClient();
      const stale = page(daysAgo(0), { prev_date: daysAgo(1) });
      const yesterday = page(daysAgo(1), { prev_date: daysAgo(3) });
      client.setQueryData<ChatPages>(qk.chatList(CHILD, null), {
        pages: [stale, yesterday],
        pageParams: [null, { date: daysAgo(1) }],
      });

      await refreshAfterRun(client, CHILD);

      const data = client.getQueryData<ChatPages>(qk.chatList(CHILD, null));
      expect(data?.pages[0].turns.map((t) => t.run_id)).toEqual(["r_hist_breakfast", "r_hist_nap"]);
      expect(data?.pages[1]).toBe(yesterday);
      expect(client.getQueryState(qk.chatList(CHILD, null))?.isInvalidated).toBe(false);
    });

    it("갈아 끼울 수 없으면 대화 목록을 낡았다고 표시한다", async () => {
      setScenario("chat_history");
      const client = new QueryClient();
      // 받아 둔 첫 장이 어제다 — 앱을 켜 둔 채 자정을 넘긴 경우.
      client.setQueryData<ChatPages>(qk.chatList(CHILD, null), {
        pages: [page(daysAgo(1), { prev_date: daysAgo(3) })],
        pageParams: [null],
      });

      await refreshAfterRun(client, CHILD);

      expect(client.getQueryState(qk.chatList(CHILD, null))?.isInvalidated).toBe(true);
    });

    it("지난 날부터 연 목록은 갈아 끼우지 않고 낡았다고만 표시한다 — 오늘 장이 붙어 있는지 모른다", async () => {
      const client = new QueryClient();
      const opened = page(daysAgo(3), { next_date: daysAgo(1) });
      client.setQueryData<ChatPages>(qk.chatList(CHILD, daysAgo(3)), {
        pages: [opened],
        pageParams: [{ date: daysAgo(3) }],
      });

      await refreshAfterRun(client, CHILD);

      const state = client.getQueryState(qk.chatList(CHILD, daysAgo(3)));
      expect(state?.isInvalidated).toBe(true);
      expect(client.getQueryData<ChatPages>(qk.chatList(CHILD, daysAgo(3)))?.pages[0]).toBe(opened);
    });

    it("대화 목록이 아닌 아이 쿼리는 그대로 무효화한다 — run 하나가 홈과 기록을 같이 바꾼다", async () => {
      const client = new QueryClient();
      client.setQueryData(qk.home(CHILD), { stale: true });
      client.setQueryData(qk.chatToday(CHILD), page(daysAgo(0)));

      await refreshAfterRun(client, CHILD);

      expect(client.getQueryState(qk.home(CHILD))?.isInvalidated).toBe(true);
      expect(client.getQueryState(qk.chatToday(CHILD))?.isInvalidated).toBe(true);
    });
  });
});
