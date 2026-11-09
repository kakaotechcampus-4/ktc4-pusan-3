import { describe, expect, it } from "vitest";

import { ApiError } from "@/lib/api/errors";

import { inputTextLength, isLengthRejected } from "./input-length";

/**
 * 한 줄 입력의 글자 수 (#867). 서버 `CreateInputRequest.text` 와 같은 법으로 센다 —
 * 앞뒤 공백을 떼고 코드 포인트 수.
 */
describe("inputTextLength", () => {
  it("앞뒤 공백 · 줄바꿈은 세지 않는다 — 보내는 글도 뗀 글이다", () => {
    expect(inputTextLength("  가나다 \n")).toBe(3);
  });

  it("🚨 이모지는 1자다 — UTF-16 으로 세면 서버가 받는 글을 화면이 막는다", () => {
    expect(inputTextLength("😀".repeat(2000))).toBe(2000);
  });
});

describe("isLengthRejected — 실패 카드가 길이 탓이라고 말할지", () => {
  const textRejected = new ApiError(400, "validation_failed", "요청 형식이 올바르지 않아요", {
    fields: ["text"],
  });
  const long = "가".repeat(2001);

  it("text 를 거절했고 보낸 글이 한도를 넘으면 참", () => {
    expect(isLengthRejected(textRejected, long)).toBe(true);
  });

  it("🚨 text 거절이어도 보낸 글이 한도 안이면 거짓 — 서버만 공백으로 떼는 글자로 빈 글이 된 경우다", () => {
    expect(isLengthRejected(textRejected, "\u0085")).toBe(false);
  });

  it("다른 필드의 거절 · 다른 코드는 거짓", () => {
    const sourceRejected = new ApiError(400, "validation_failed", "요청 형식이 올바르지 않아요", {
      fields: ["source"],
    });
    const limit = new ApiError(429, "daily_input_limit", "오늘은 더 보낼 수 없어요");
    expect(isLengthRejected(sourceRejected, long)).toBe(false);
    expect(isLengthRejected(limit, long)).toBe(false);
    expect(isLengthRejected(new Error("x"), long)).toBe(false);
  });
});
