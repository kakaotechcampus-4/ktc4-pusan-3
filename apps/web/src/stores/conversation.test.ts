import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { partialMessage } from "@/components/run-result";
import { ApiError, NetworkError, isApiError } from "@/lib/api/errors";
import { initialRunState } from "@/hooks/use-run-stream";
import { newIdempotencyKey } from "@/lib/api/idempotency";
import { submittedInput } from "@/mocks/handlers/runs";
import { setScenario, type Scenario } from "@/mocks/scenario";
import { useDraftStore } from "@/stores/draft";

import {
  canRetry,
  conversationOf,
  hasOpenQuestion,
  isBusy,
  retryTurn,
  rewriteTurn,
  sendLine,
  stopAnswering,
  answerQuestion,
  useConversationStore,
  type ChatTurn,
} from "./conversation";

/**
 * 04 대화 (#226) — 화면 밖에서 도는 보내기 · 구독 · 끝 처리를 **목 서버와 실제로 돌려서** 건다.
 *
 * 화면 테스트가 아니다. 여기서 거는 것은 리뷰에서 세 번 버그가 났던 자리(#141 · #158 · #175)가
 * 대화 상태로 옮겨 오면서 그대로 지켜지는가 — 답이 **어느 run** 을 가리키는가, 질문을 **언제** 닫는가,
 * 다시 시도가 **같은 키**로 나가는가 — 다.
 */

const CHILD = "c1";

/** 기본 대본 하나가 3초쯤 걸린다 — run 둘을 잇는 테스트가 기본 5초를 넘는다. */
const TWO_RUNS = 15_000;

function conversation() {
  return conversationOf(useConversationStore.getState(), CHILD);
}

function turn(id: string): ChatTurn {
  const found = conversation().turns.find((t) => t.id === id);
  if (!found) throw new Error("말풍선이 없다");
  return found;
}

function runIdOf(id: string): string {
  const { send } = turn(id);
  if (send.status !== "sent") throw new Error("아직 run 이 없다");
  return send.runId;
}

/** 목 대본은 진짜 시간으로 흐른다 (sleep). 날짜만 속이는 테스트가 있어 `performance` 로 잰다. */
async function until(check: () => boolean, timeoutMs = 10_000): Promise<void> {
  const started = performance.now();
  while (!check()) {
    if (performance.now() - started > timeoutMs) throw new Error("기다리다 끝났다");
    await new Promise((resolve) => setTimeout(resolve, 20));
  }
}

/** 말풍선 하나가 보내기 · run 까지 끝날 때까지. */
async function settled(id: string): Promise<ChatTurn> {
  await until(() => {
    const t = turn(id);
    // 🚨 다시 보내는 동안에는 앞 run 이 그대로 남아 있다 — 보내기가 끝났는지부터 본다.
    if (t.send.status === "sending") return false;
    return t.send.status === "error" || !["idle", "streaming"].includes(t.run.status);
  });
  return turn(id);
}

function send(text: string, asReply = false): string {
  const id = sendLine(CHILD, text, { asReply, source: "home_input" });
  if (!id) throw new Error("보내지 못했다");
  return id;
}

function scenario(name: Scenario) {
  setScenario(name);
}

beforeEach(() => {
  useConversationStore.getState().clearAll();
  useDraftStore.getState().clearAll();
  scenario("default");
});

afterEach(() => {
  vi.useRealTimers();
});

describe("보내는 순간", () => {
  it("말풍선이 202 보다 먼저 선다 · 그동안 다음 한 줄은 못 보낸다", async () => {
    const id = send("지어낸 한 줄");

    expect(turn(id).send.status).toBe("sending");
    expect(turn(id).body.text).toBe("지어낸 한 줄");
    expect(isBusy(conversation())).toBe(true);
    // 🚨 한 번에 하나 — 두 run 이 겹치면 질문을 언제 닫는지가 도착 순서에 달린다.
    expect(sendLine(CHILD, "또 한 줄", { asReply: false, source: "home_input" })).toBeNull();

    const done = await settled(id);
    expect(done.run.status).toBe("done");
    expect(isBusy(conversation())).toBe(false);
  });

  it(
    "같은 날 두 번째 한 줄은 첫 대화 아래에 이어 붙는다",
    async () => {
      const first = send("지어낸 한 줄");
      await settled(first);
      const second = send("두 번째 한 줄");
      await settled(second);

      expect(conversation().turns.map((t) => t.id)).toEqual([first, second]);
      expect(turn(second).day).toBe(turn(first).day);
    },
    TWO_RUNS,
  );
});

describe("되묻기에 대화 안에서 답한다", () => {
  it("답의 `reply_to` 는 그 질문을 낸 run 이고, 처리되면 질문이 닫힌다", async () => {
    scenario("note_question");
    const asked = send("어제부터 기침해");
    await settled(asked);

    expect(turn(asked).question).toBe("open");
    expect(conversation().answering?.runId).toBe(runIdOf(asked));

    const reply = send("사흘 전부터", true);
    expect(turn(reply).body.reply_to).toBe(runIdOf(asked));
    await settled(reply);

    // 🚨 원문을 다시 보내지 않는다 (#158) — 서버가 받은 본문은 답 하나와 가리키는 run 뿐이다.
    expect(submittedInput(runIdOf(reply))).toMatchObject({
      text: "사흘 전부터",
      replyTo: runIdOf(asked),
    });
    expect(turn(asked).question).toBe("answered");
    expect(conversation().answering).toBeNull();
    expect(hasOpenQuestion(conversation())).toBe(false);
  });

  it("이어받기 run 이 또 물으면 다음 답은 **그 run** 을 가리킨다", async () => {
    scenario("reply_asks_again");
    const asked = send("어제부터 기침해");
    await settled(asked);

    const reply = send("사흘 전부터", true);
    await settled(reply);
    // 처음 질문은 닫히고, 이어받은 run 이 낸 질문이 입력창의 새 대상이 된다.
    expect(turn(asked).question).toBe("answered");
    expect(turn(reply).question).toBe("open");
    expect(conversation().answering?.runId).toBe(runIdOf(reply));

    const again = send("하루 세 번쯤", true);
    expect(turn(again).body.reply_to).toBe(runIdOf(reply));
    const done = await settled(again);
    expect(done.run.status).toBe("done");
    expect(done.run.observations.length).toBeGreaterThan(0);
    expect(hasOpenQuestion(conversation())).toBe(false);
  });

  it("🚨 답을 내려놓아도 질문은 열린 채로 남고, 다시 골라 답할 수 있다", async () => {
    scenario("note_question");
    const asked = send("어제부터 기침해");
    await settled(asked);

    stopAnswering(CHILD);
    expect(conversation().answering).toBeNull();
    // 예전 "나중에 할게요" 는 질문과 runId 를 지워서 다시 답할 방법이 없었다 (#226).
    expect(turn(asked).question).toBe("open");

    answerQuestion(CHILD, runIdOf(asked));
    expect(conversation().answering?.runId).toBe(runIdOf(asked));
  });

  it("자정을 넘겨 보낸 답은 질문이 나온 날에 붙는다 · 새 한 줄은 새 날이다", async () => {
    scenario("note_question");
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-10-05T14:50:00Z")); // 한국 23:50

    const asked = send("어제부터 기침해");
    await settled(asked);
    expect(turn(asked).day).toBe("2026-10-05");

    vi.setSystemTime(new Date("2026-10-05T15:10:00Z")); // 한국 다음 날 00:10
    const reply = send("사흘 전부터", true);
    expect(turn(reply).day).toBe("2026-10-05");
    await settled(reply);

    const next = send("새 이야기");
    expect(turn(next).day).toBe("2026-10-06");
    await settled(next);
  });
});

describe("실패는 보낸 말풍선 아래에서 다시 시도 · 고쳐 쓰기", () => {
  it("🚨 이어받기 run 이 실패하면 다시 시도가 같은 키 · 같은 `reply_to` 로 나가 저장된다", async () => {
    scenario("reply_failed");
    const asked = send("어제부터 기침해");
    await settled(asked);

    const reply = send("사흘 전부터", true);
    const failed = await settled(reply);
    expect(failed.run.status).toBe("failed");
    expect(canRetry(failed)).toBe(true);
    // 🚨 실패에서 질문을 닫지 않는다 (#175) — 서버가 맥락을 되돌려 뒀다.
    expect(turn(asked).question).toBe("open");

    const key = failed.key;
    retryTurn(CHILD, reply);
    const retried = await settled(reply);

    expect(retried.key).toBe(key);
    expect(submittedInput(runIdOf(reply))?.replyTo).toBe(runIdOf(asked));
    expect(retried.run.observations.length).toBeGreaterThan(0);
    expect(turn(asked).question).toBe("answered");
  });

  it("400 reply_context_unavailable — 다시 시도가 없고 질문을 놓는다 · 다음 한 줄은 새 입력이다", async () => {
    scenario("reply_unavailable");
    const asked = send("어제부터 기침해");
    await settled(asked);

    const reply = send("사흘 전부터", true);
    const failed = await settled(reply);
    expect(failed.send.status === "error" && isApiError(failed.send.error)).toBe(true);
    expect(canRetry(failed)).toBe(false);
    expect(turn(asked).question).toBe("dropped");
    expect(conversation().answering).toBeNull();

    // 고쳐 쓰기 — 원문이 입력창으로 돌아가고, 닿지 못한 말풍선은 자리를 비운다.
    rewriteTurn(CHILD, reply);
    expect(useDraftStore.getState().byChild[CHILD]).toBe("사흘 전부터");
    expect(conversation().turns.some((t) => t.id === reply)).toBe(false);

    const fresh = send("사흘 전부터 기침했어요", true);
    expect(turn(fresh).body.reply_to).toBeUndefined();
  });

  it("429 daily_input_limit — 다시 시도가 없고 원문은 입력창에 되돌아간다", async () => {
    scenario("daily_limit");
    const id = send("지어낸 한 줄");
    const failed = await settled(id);

    expect(
      failed.send.status === "error" && isApiError(failed.send.error, "daily_input_limit"),
    ).toBe(true);
    expect(canRetry(failed)).toBe(false);
    expect(useDraftStore.getState().byChild[CHILD]).toBe("지어낸 한 줄");
  });

  it("🚨 저장됐는지 모르는 run(unconfirmed)은 말풍선을 남기고, 같은 본문은 같은 키로 나간다", async () => {
    scenario("disconnected");
    const id = send("지어낸 한 줄");
    const lost = await settled(id);
    expect(lost.run.status).toBe("unconfirmed");
    expect(canRetry(lost)).toBe(true);

    rewriteTurn(CHILD, id);
    // 서버가 저장했을 수 있어서 대화에서 지우지 않는다. 같은 자리의 다시 시도만 내린다.
    expect(turn(id).handedBack).toBe(true);
    expect(canRetry(turn(id))).toBe(false);

    // 🚨 고치지 않고 그대로 다시 보내면 같은 키다 — 새 키면 서버가 이미 저장한 한 줄이 두 번 저장된다.
    const again = send(useDraftStore.getState().byChild[CHILD] ?? "");
    expect(turn(again).key).toBe(lost.key);
    await settled(again);
  });

  it(
    "끝까지 처리된 뒤에는 같은 문장도 새 키다",
    async () => {
      const first = send("지어낸 한 줄");
      await settled(first);
      const second = send("지어낸 한 줄");
      expect(turn(second).key).not.toBe(turn(first).key);
      await settled(second);
    },
    TWO_RUNS,
  );
});

describe("🚨 원문은 입력창에 옮겼을 때만 말풍선을 떠난다 (#231 리뷰)", () => {
  it(
    "응답을 못 받은 말풍선은 다음 한 줄이 대체하지 않는다 · 같은 키로 다시 시도하면 닿는다",
    async () => {
      scenario("send_failed");
      const lost = send("지어낸 한 줄");
      const failed = await settled(lost);
      expect(failed.send.status).toBe("error");
      expect(canRetry(failed)).toBe(true);

      // 예전엔 여기서 줄1 이 지워졌다 — 보낼 때 입력창을 비웠으니 원문이 어디에도 안 남았다.
      const next = send("다른 한 줄");
      await settled(next);
      expect(turn(lost).body.text).toBe("지어낸 한 줄");

      const key = turn(lost).key;
      retryTurn(CHILD, lost);
      const retried = await settled(lost);
      expect(retried.key).toBe(key);
      expect(retried.run.status).toBe("done");
    },
    TWO_RUNS,
  );

  it("입력창에 쓰던 글이 있으면 고쳐 쓰기가 아무것도 하지 않는다 (덮지도 지우지도 않는다)", async () => {
    scenario("send_failed");
    const lost = send("지어낸 한 줄");
    await settled(lost);
    useDraftStore.getState().setDraft(CHILD, "쓰던 글");

    expect(rewriteTurn(CHILD, lost)).toBe(false);
    expect(useDraftStore.getState().byChild[CHILD]).toBe("쓰던 글");
    expect(turn(lost).body.text).toBe("지어낸 한 줄");
  });

  it("429 에 입력창이 차 있으면 원문을 옮기지 않고 말풍선에 남긴다", async () => {
    scenario("daily_limit");
    useDraftStore.getState().setDraft(CHILD, "쓰던 글");
    const id = send("지어낸 한 줄");
    const failed = await settled(id);

    expect(failed.handedBack).toBe(false);
    expect(useDraftStore.getState().byChild[CHILD]).toBe("쓰던 글");
    // 다음 한 줄도 이 말풍선을 지우지 않는다 — 여기가 원문의 자리다.
    const next = send("또 한 줄");
    await settled(next);
    expect(turn(id).body.text).toBe("지어낸 한 줄");
  });

  it("202 를 받은 줄은 다시 시도가 네트워크로 실패해도 결과 미확인으로 남는다", async () => {
    scenario("disconnected");
    const id = send("지어낸 한 줄");
    const lost = await settled(id);
    expect(lost.run.status).toBe("unconfirmed");
    const runId = runIdOf(id);

    scenario("send_failed");
    retryTurn(CHILD, id);
    const again = await settled(id);
    expect(again.send).toEqual({ status: "sent", runId });
    expect(again.run.status).toBe("unconfirmed");
    expect(canRetry(again)).toBe(true);
  });
});

describe("🚨 같은 본문은 끝나기 전까지 같은 키 · 한 번에 한 run (#231 리뷰)", () => {
  it(
    "결과 미확인 줄이 있는데 다른 줄을 보낸 뒤 그 줄을 그대로 다시 보내도 같은 키다",
    async () => {
      scenario("disconnected");
      const first = send("지어낸 한 줄");
      const lost = await settled(first);

      scenario("default");
      const other = send("다른 한 줄");
      await settled(other);

      // 한동안 키 보관함이 본문 하나만 기억해서 여기서 새 키가 나갔다 — 서버가 줄1 을 저장했다면 두 번 저장.
      rewriteTurn(CHILD, first);
      const again = send(useDraftStore.getState().byChild[CHILD] ?? "");
      expect(turn(again).key).toBe(lost.key);
      await settled(again);
    },
    TWO_RUNS * 2,
  );

  it(
    "앞 줄을 다시 시도하는 동안에는 마지막 줄이 끝나 있어도 새 줄을 못 보낸다",
    async () => {
      scenario("disconnected");
      const first = send("지어낸 한 줄");
      await settled(first);

      scenario("default");
      const other = send("다른 한 줄");
      await settled(other);

      retryTurn(CHILD, first);
      expect(isBusy(conversation())).toBe(true);
      expect(sendLine(CHILD, "또 한 줄", { asReply: false, source: "home_input" })).toBeNull();
      await settled(first);
    },
    TWO_RUNS * 2,
  );
});

describe("다시 시도는 응답을 못 받은 경우의 것이다 (#231 리뷰)", () => {
  function failedWith(error: unknown): ChatTurn {
    return {
      id: "t",
      day: "2026-10-05",
      body: { text: "지어낸 한 줄", source: "home_input" },
      answering: null,
      key: newIdempotencyKey(),
      send: { status: "error", error },
      run: initialRunState,
      question: null,
      handedBack: false,
    };
  }

  it("서버가 거절한 4xx 에는 다시 시도가 없다 (형식 · 권한 · 한도 · 맥락)", () => {
    for (const error of [
      new ApiError(422, "validation_error", "2000자를 넘을 수 없어요"),
      new ApiError(403, "child_access_denied", "이 아이를 볼 수 없어요"),
      new ApiError(429, "daily_input_limit", "오늘은 더 적을 수 없어요"),
      new ApiError(400, "reply_context_unavailable", "이어서 확인할 수 없어요"),
    ]) {
      expect(canRetry(failedWith(error)), error.code).toBe(false);
    }
  });

  it("네트워크 · 5xx 에는 있다 — 서버가 받았을 수 있어 같은 키로 다시 보낸다", () => {
    expect(canRetry(failedWith(new NetworkError(new Error("끊김"))))).toBe(true);
    expect(canRetry(failedWith(new ApiError(503, "llm_unavailable", "잠시 뒤에")))).toBe(true);
  });
});

describe("부분 결과 문구 (PR #215 리뷰)", () => {
  it("저장한 것이 없으면 '저장' 을 말하지 않는다", () => {
    const text = partialMessage({ reason: "timeout_20s", succeeded: [], failed: ["food"] }, false);
    expect(text).not.toContain("저장");
    expect(text).toContain("처리하지 못했어요");
  });

  it("같은 Agent 가 양쪽에 있으면 '일부' 다", () => {
    const text = partialMessage(
      { reason: "timeout_20s", succeeded: ["food"], failed: ["food", "activity"] },
      true,
    );
    expect(text).toMatch(/쪽 일부, .+ 쪽은 이번에 처리하지 못했어요\./);
    expect(text).toContain("저장한 기록은 그대로");
  });
});
