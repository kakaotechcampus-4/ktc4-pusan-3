import { isApiError } from "@/lib/api/errors";
import { INPUT_TEXT_MAX_LENGTH } from "@/lib/api/types";

/**
 * 03 홈 · 04 대화 한 줄 입력의 글자 수 (#867).
 *
 * 🚨 **서버와 같은 법으로 센다** — 앞뒤 공백을 떼고(`sendLine` 도 뗀 글을 보낸다) 코드 포인트 수로 센다.
 *    09 일기 · 10 문제 알리기(#549)는 `maxLength` 와 같은 UTF-16 단위로 세는데, 이 입력은 `maxLength` 를
 *    걸지 않아서 맞출 상대가 브라우저가 아니라 서버다. UTF-16 으로 세면 이모지 하나가 2자라 서버가 받는
 *    글을 막는다.
 */
export function inputTextLength(text: string): number {
  return [...text.trim()].length;
}

/** 이 글자 수부터 카운터를 보인다. 대부분의 한 줄은 수십 자라 늘 띄우면 채팅바 위가 숫자로 붐빈다. */
export const INPUT_COUNTER_FROM = INPUT_TEXT_MAX_LENGTH - 200;

/**
 * 서버가 이 한 줄을 **길이 때문에** 거절했는가 — 04 실패 카드가 서버 문구 대신 이유를 말할지 (#867).
 *
 * 400 `validation_failed` 에 `detail.fields` 가 `text` 를 가리키고, 🚨 **보낸 글이 정말 한도를 넘을 때만** 참이다.
 * `text` 거절은 빈 글일 수도 있다 — 서버는 JS `trim()` 이 안 떼는 글자(U+0085 등)도 공백으로 떼서, 화면에서는
 * 1자인 글이 서버에서는 빈 글로 거절된다. 그때 "길어서" 라고 말하면 틀린 말이라 서버 문구로 돌아간다.
 */
export function isLengthRejected(error: unknown, text: string): boolean {
  if (!isApiError(error, "validation_failed")) return false;
  const fields = error.detail?.fields;
  if (!Array.isArray(fields) || !fields.includes("text")) return false;
  return inputTextLength(text) > INPUT_TEXT_MAX_LENGTH;
}
