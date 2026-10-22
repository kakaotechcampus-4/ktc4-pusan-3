import { describe, expect, it } from "vitest";

import type { AgentReadout, AgentResultEvent, PartialEvent } from "@/lib/api/sse";
import type { SuggestionStatus } from "@/lib/api/types";

import {
  blockCandidates,
  blockReadouts,
  partialRetryHref,
  visibleAgentResults,
} from "./agent-result";

const result = (over: Partial<AgentResultEvent> = {}): AgentResultEvent => ({
  agent: "food",
  task_type: "meal_recommendation",
  status: "completed",
  readouts: [],
  question: null,
  suggestion_ids: [],
  kind: null,
  reason: null,
  record_count: null,
  ...over,
});

const notice = (body: string, code: AgentReadout["code"]): AgentReadout => ({
  kind: "notice",
  title: "",
  body,
  authored_by: "code",
  code,
});

const WEATHER = notice("오늘은 바깥 활동이 어려운 날이라 실내 놀이만 골랐어요.", null);
const FEWER = notice("조건에 맞는 추천을 1개 준비했어요.", "fewer");
const EMPTY = notice("조건에 맞는 추천이 없어요.", "empty");
const place = { childId: "c1", runId: "r1" };

describe("visibleAgentResults", () => {
  it("추천만 있는 결과도 블록이 선다 — readout · 되묻기가 없어도", () => {
    expect(visibleAgentResults([result({ suggestion_ids: ["s_1"] })])).toHaveLength(1);
  });

  it("보일 것이 없는 결과(쓰는 task)는 블록을 세우지 않는다", () => {
    expect(visibleAgentResults([result({ task_type: "daycare_meal" })])).toEqual([]);
  });
});

describe("blockReadouts", () => {
  it("🚨 후보가 있으면 개수 안내는 '후보 N개' 가 대신하고, 날씨 안내는 그대로 선다", () => {
    const shown = blockReadouts(result({ suggestion_ids: ["s_1"], readouts: [FEWER, WEATHER] }));
    expect(shown).toEqual([WEATHER]);
  });

  it("후보가 없으면 0개 안내를 그대로 그린다", () => {
    expect(blockReadouts(result({ readouts: [EMPTY] }))).toEqual([EMPTY]);
  });
});

describe("blockCandidates", () => {
  it("후보가 없으면 칸이 없다", () => {
    expect(blockCandidates(result(), place)).toBeNull();
  });

  it("개인화 묶음은 개수 · 이유와 그 Agent · 그 run 의 05 주소를 낸다", () => {
    const candidates = blockCandidates(
      result({
        suggestion_ids: ["s_1", "s_2", "s_3"],
        kind: "personalized",
        reason: "아이 기록 2건을 보고 골랐어요.",
      }),
      place,
    );
    expect(candidates).toEqual({
      count: 3,
      reason: "아이 기록 2건을 보고 골랐어요.",
      general: false,
      recordCount: null,
      href: "/child/c1/suggestions?agents=food&run=r1",
      closed: false,
    });
  });

  it("🚨 일반 묶음은 쌓인 기록 0건도 숨기지 않는다", () => {
    const candidates = blockCandidates(
      result({ agent: "activity", suggestion_ids: ["g_1"], kind: "general", record_count: 0 }),
      place,
    );
    expect(candidates?.general).toBe(true);
    expect(candidates?.recordCount).toBe(0);
  });

  it("run 을 모르면 링크를 내지 않는다 — 못 가는 링크는 그리지 않는다", () => {
    const candidates = blockCandidates(result({ suggestion_ids: ["s_1"] }), {
      childId: "c1",
      runId: null,
    });
    expect(candidates?.href).toBeNull();
  });

  describe("지난 대화 — 추천의 지금 상태로 가른다 (#228)", () => {
    const withStates = (...statuses: SuggestionStatus[]) =>
      result({
        suggestion_ids: statuses.map((_, i) => `s_${i}`),
        suggestions: statuses.map((status, i) => ({ id: `s_${i}`, status })),
      });

    it("고를 수 있거나(draft) 05 에서 보이는(approved) 것이 하나라도 있으면 연다", () => {
      expect(blockCandidates(withStates("expired", "draft"), place)?.href).not.toBeNull();
      expect(blockCandidates(withStates("approved", "rejected"), place)?.href).not.toBeNull();
    });

    it("🚨 다 지났거나 거절했으면 링크 대신 닫혔다고 말한다 — 눌러도 이어서 할 수 없다", () => {
      const candidates = blockCandidates(withStates("expired", "rejected"), place);
      expect(candidates?.href).toBeNull();
      expect(candidates?.closed).toBe(true);
      // 개수와 이유는 그대로 선다 — 그날 무엇을 받았는지는 지난 대화의 일부다.
      expect(candidates?.count).toBe(2);
    });

    it("행을 찾지 못해 상태가 하나도 없으면 고를 수 있는 것이 없다", () => {
      const candidates = blockCandidates(
        result({ suggestion_ids: ["s_gone"], suggestions: [] }),
        place,
      );
      expect(candidates?.closed).toBe(true);
    });
  });
});

describe("partialRetryHref", () => {
  const partial = (retryable: PartialEvent["retryable"]): PartialEvent => ({
    reason: "agent_error",
    succeeded: ["food"],
    failed: ["activity"],
    retryable,
  });

  it("실패한 추천 Agent 들로 그 run 의 05 를 연다", () => {
    expect(partialRetryHref(partial(["activity"]), place)).toBe(
      "/child/c1/suggestions?agents=activity&run=r1",
    );
  });

  it("다시 준비할 Agent 가 없으면(급식 갱신만 실패) 링크가 없다", () => {
    expect(partialRetryHref(partial([]), place)).toBeNull();
  });

  it("run 을 모르면 링크가 없다", () => {
    expect(partialRetryHref(partial(["activity"]), { childId: "c1", runId: null })).toBeNull();
  });
});
