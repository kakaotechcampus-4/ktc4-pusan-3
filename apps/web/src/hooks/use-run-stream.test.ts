import { describe, expect, it } from "vitest";

import { idempotentPath, newIdempotencyKey } from "@/lib/api/idempotency";
import { api } from "@/lib/api/client";
import { streamRunEvents, type RunEvent } from "@/lib/api/sse";
import { setScenario } from "@/mocks/scenario";

import {
  initialRunState,
  isRunConfirmed,
  pumpRunEvents,
  runReducer,
  type RunAction,
  type RunState,
} from "./use-run-stream";

/**
 * run 이 **어떻게 끝났는가**.
 *
 * 멘토 리뷰(#71): "`streamRunEvents()` 는 reader 가 EOF 를 반환하면 예외 없이 반복을 끝냅니다.
 * 서버나 프록시가 `done`/`failed` 이벤트를 보내기 전에 연결을 닫으면 (…) 상태는 `streaming` 에
 * 남습니다. 홈 화면은 계속 진행 화면만 보여주며 20초 타임아웃도 더 이상 동작하지 않습니다."
 *
 * 그래서 아래가 거는 것은 두 가지다.
 *   ① 종료 이벤트 없이 끝난 스트림이 **진행 상태에 남지 않는다**
 *   ② 그 끝을 **부분 성공으로 속이지 않는다** — 저장 여부를 모르는 상태는 따로 있다
 *      (그 구분이 03 홈에서 "입력 원문을 비워도 되는가" 를 가른다)
 */

const STREAMING: RunState = { ...initialRunState, status: "streaming" };

/** 이벤트 목록을 그대로 흘려 최종 상태를 만든다. `runReducer` 가 순수 함수라 가능하다. */
function fold(events: RunEvent[], from: RunState = STREAMING): RunState {
  return events.reduce((state, event) => runReducer(state, { type: "event", event }), from);
}

async function* emit(...events: RunEvent[]): AsyncGenerator<RunEvent> {
  for (const event of events) yield event;
}

/** pump 가 낸 액션을 순서대로 모은다. */
async function actionsOf(source: AsyncIterable<RunEvent>): Promise<RunAction["type"][]> {
  const seen: RunAction["type"][] = [];
  await pumpRunEvents(source, (action) => seen.push(action.type));
  return seen;
}

const STEP: RunEvent = { type: "step", data: { index: 1, total: 3, label: "보고 있어요" } };
const DONE: RunEvent = { type: "done", data: { run_id: "r1", model_calls: 2 } };
const FAILED: RunEvent = {
  type: "failed",
  data: { reason: "unparsable", raw_text: "지어낸 한 줄" },
};

describe("종료 이벤트 없이 닫힌 스트림", () => {
  it("step 하나 뒤 EOF 면 closed 를 낸다", async () => {
    // 리뷰가 재현한 그 스트림이다.
    expect(await actionsOf(emit(STEP))).toEqual(["event", "closed"]);
  });

  it("이벤트가 하나도 없이 닫혀도 closed 를 낸다", async () => {
    expect(await actionsOf(emit())).toEqual(["closed"]);
  });

  it("done 으로 끝나면 closed 를 내지 않는다", async () => {
    expect(await actionsOf(emit(STEP, DONE))).toEqual(["event", "event"]);
  });

  it("failed 로 끝나면 closed 를 내지 않는다", async () => {
    expect(await actionsOf(emit(STEP, FAILED))).toEqual(["event", "event"]);
  });
});

describe("저장 여부를 모르는 끝 — unconfirmed", () => {
  it("closed 는 streaming 을 unconfirmed 로 옮긴다", () => {
    const state = runReducer(fold([STEP]), { type: "closed" });
    expect(state.status).toBe("unconfirmed");
  });

  it("20초 침묵도 같은 자리로 간다", () => {
    expect(runReducer(STREAMING, { type: "timeout" }).status).toBe("unconfirmed");
  });

  it("연결 실패도 같은 자리로 간다 — 서버가 실패라고 말한 것이 아니다", () => {
    const state = runReducer(STREAMING, { type: "error" });
    expect(state.status).toBe("unconfirmed");
    // 🚨 failure 를 지어내면 화면이 "아무것도 저장하지 않았어요" 라고 단정한다.
    expect(state.failure).toBeNull();
  });

  it("partial 을 지어내지 않는다 — 어느 Agent 가 성공했는지 모른다", () => {
    for (const action of [{ type: "closed" } as const, { type: "timeout" } as const]) {
      // 지어낸 partial 은 화면에서 빈 목록으로 "○○ 쪽은 준비하지 못했어요" 가 된다.
      expect(runReducer(STREAMING, action).partial).toBeNull();
    }
  });

  it("받아 둔 것은 그대로 들고 있는다", () => {
    const saved: RunEvent = {
      type: "saved",
      data: { observations: [{ id: "o1" }, { id: "o2" }] },
    } as RunEvent;
    const state = runReducer(fold([saved]), { type: "closed" });
    expect(state.observations).toHaveLength(2);
  });

  it("이미 끝난 run 을 덮지 않는다", () => {
    const done = fold([STEP, DONE]);
    expect(runReducer(done, { type: "closed" }).status).toBe("done");
    expect(runReducer(done, { type: "timeout" }).status).toBe("done");
  });
});

describe("입력 원문을 비워도 되는가 — isRunConfirmed", () => {
  it("서버가 끝을 말한 경우에만 true", () => {
    expect(isRunConfirmed("done")).toBe(true);
    expect(isRunConfirmed("partial")).toBe(true);
  });

  it("🚨 unconfirmed 는 false — 저장됐는지 모르는 채로 원문을 지우지 않는다", () => {
    expect(isRunConfirmed("unconfirmed")).toBe(false);
  });

  it("실패·진행 중도 false", () => {
    for (const status of ["failed", "streaming", "idle"] as const) {
      expect(isRunConfirmed(status)).toBe(false);
    }
  });
});

describe("목 스트림 — 실제로 끊어 본다", () => {
  it("disconnected 시나리오는 종료 이벤트 없이 닫히고, 화면 상태는 unconfirmed 가 된다", async () => {
    setScenario("disconnected");
    try {
      const { run_id } = await api.post<{ run_id: string }>(
        idempotentPath.input("c1"),
        { text: "지어낸 한 줄", source: "home_input" },
        { idempotencyKey: newIdempotencyKey() },
      );

      let state: RunState = { ...initialRunState, status: "streaming" };
      const seen: string[] = [];
      await pumpRunEvents(streamRunEvents(run_id), (action) => {
        if (action.type === "event") seen.push(action.event.type);
        state = runReducer(state, action);
      });

      expect(seen).not.toContain("done");
      expect(seen).not.toContain("failed");
      expect(state.status).toBe("unconfirmed");
    } finally {
      setScenario("default");
    }
  });
});
