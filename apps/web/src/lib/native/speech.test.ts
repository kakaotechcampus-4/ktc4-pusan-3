import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  SPEECH_EVENT,
  appendSpoken,
  isSpeechEvent,
  readSpeechSupport,
  subscribeSpeech,
} from "./speech";

/**
 * 말로 적기의 웹 쪽 약속 (#595). 셸이 보내는 쪽은 `apps/mobile/src/native/speech.test.ts` 가 건다.
 */

// vitest 가 node 환경이라 `window` 가 없다. 셸이 이벤트를 쏘는 자리만 있으면 되므로 `EventTarget` 으로 세운다.
beforeEach(() => {
  vi.stubGlobal("window", new EventTarget());
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("readSpeechSupport", () => {
  it("셸이 없으면 none 이다 (브라우저)", () => {
    expect(readSpeechSupport()).toBe("none");
  });

  it("셸이 기기 안 인식을 못 한다고 하면 unavailable 이다", () => {
    window.icatch = { speech: { available: false, start() {}, stop() {} } };
    expect(readSpeechSupport()).toBe("unavailable");
  });

  it("셸이 쓸 수 있다고 하면 ready 다", () => {
    window.icatch = { speech: { available: true, start() {}, stop() {} } };
    expect(readSpeechSupport()).toBe("ready");
  });
});

describe("appendSpoken", () => {
  it("빈 입력창에는 들은 말만 넣는다", () => {
    expect(appendSpoken("", " 오늘 공원에 갔어요 ")).toBe("오늘 공원에 갔어요");
  });

  it("글 뒤에는 한 칸 띄워 붙인다 — 앞 낱말과 붙지 않게", () => {
    expect(appendSpoken("아침에", "밥을 잘 먹었어요")).toBe("아침에 밥을 잘 먹었어요");
  });

  it("글이 공백이나 줄바꿈으로 끝나면 더 띄우지 않는다", () => {
    expect(appendSpoken("아침에 ", "밥")).toBe("아침에 밥");
    expect(appendSpoken("아침에\n", "밥")).toBe("아침에\n밥");
  });

  it("들은 말이 비었으면 글을 그대로 둔다", () => {
    expect(appendSpoken("아침에", "  ")).toBe("아침에");
  });
});

describe("isSpeechEvent", () => {
  it("약속한 여섯만 통과한다", () => {
    expect(isSpeechEvent({ type: "asking" })).toBe(true);
    expect(isSpeechEvent({ type: "volume", level: 0.4 })).toBe(true);
    expect(isSpeechEvent({ type: "error", reason: "declined" })).toBe(true);
    expect(isSpeechEvent({ type: "start" })).toBe(true);
    expect(isSpeechEvent({ type: "end" })).toBe(true);
    expect(isSpeechEvent({ type: "result", text: "가", final: false })).toBe(true);
    expect(isSpeechEvent({ type: "error", reason: "denied" })).toBe(true);
  });

  it("모양이 어긋나면 버린다 — 입력창에 undefined 가 찍히면 안 된다", () => {
    expect(isSpeechEvent(null)).toBe(false);
    expect(isSpeechEvent("start")).toBe(false);
    expect(isSpeechEvent({ type: "result", final: true })).toBe(false);
    expect(isSpeechEvent({ type: "result", text: "가" })).toBe(false);
    expect(isSpeechEvent({ type: "error", reason: "network" })).toBe(false);
    expect(isSpeechEvent({ type: "record" })).toBe(false);
    // 음량은 0~1 이다 — 셸이 자른 값만 막대가 쓴다.
    expect(isSpeechEvent({ type: "volume" })).toBe(false);
    expect(isSpeechEvent({ type: "volume", level: 1.5 })).toBe(false);
    expect(isSpeechEvent({ type: "volume", level: "0.4" })).toBe(false);
  });
});

describe("subscribeSpeech", () => {
  it("모양이 맞는 이벤트만 넘기고, 끊은 뒤에는 받지 않는다", () => {
    const handler = vi.fn();
    const unsubscribe = subscribeSpeech(handler);
    const fire = (detail: unknown) =>
      window.dispatchEvent(new CustomEvent(SPEECH_EVENT, { detail }));

    fire({ type: "result", text: "가", final: true });
    fire({ type: "result" });
    expect(handler).toHaveBeenCalledTimes(1);
    expect(handler).toHaveBeenCalledWith({ type: "result", text: "가", final: true });

    unsubscribe();
    fire({ type: "end" });
    expect(handler).toHaveBeenCalledTimes(1);
  });
});
