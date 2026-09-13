import { authHeaders, buildUrl } from "./client";
import { NetworkError } from "./errors";
import type { Affinity, Agent, Observation, Ref } from "./types";

/**
 * GET /runs/{rid}/events — 04 오버레이.
 *
 * EventSource 를 쓰지 않는 이유: Authorization 헤더를 붙일 수 없다 (NF-09 는 예외를 두지 않는다).
 * 그래서 fetch + ReadableStream 으로 직접 프레임을 읽는다.
 *
 * 🚨 이벤트는 도착 순서대로 그린다. saved 는 promoted 보다 항상 먼저 온다 —
 *    저장이 검색보다 먼저이기 때문이다 (CLAUDE.md §4).
 */

export interface StepEvent {
  index: number;
  total: number;
  label: string;
}

export interface SavedEvent {
  observations: Observation[];
}

export interface PromotedEvent {
  changes: Array<{
    ref: Ref;
    merge_key: string;
    state_before: Affinity["state"];
    state_after: Affinity["state"];
    state_reason: string;
  }>;
}

export interface OfferEvent {
  options: Array<{ agent: Agent; label: string }>;
}

/** NF-06 — Agent 2개 중 1개만 성공해도 그 화면을 보여준다. 전체 실패로 떨어지면 위반이다. */
export interface PartialEvent {
  reason: string;
  succeeded: Agent[];
  failed: Agent[];
}

/**
 * 🚨 실패는 200 + 빈 배열이 아니다.
 * raw_text 를 돌려받아야 "적어주신 말은 입력창에 그대로 남겨뒀어요" 가 성립한다.
 */
export interface FailedEvent {
  reason: "unparsable" | "no_child_observation" | "llm_unavailable" | (string & {});
  raw_text: string;
}

/** model_calls 가 3 을 넘으면 서버 알람이다 (NF-01). */
export interface DoneEvent {
  run_id: string;
  model_calls: number;
}

export type RunEvent =
  | { type: "step"; data: StepEvent }
  | { type: "saved"; data: SavedEvent }
  | { type: "promoted"; data: PromotedEvent }
  | { type: "offer"; data: OfferEvent }
  | { type: "partial"; data: PartialEvent }
  | { type: "failed"; data: FailedEvent }
  | { type: "done"; data: DoneEvent }
  | { type: string; data: unknown };

/** SSE 프레임 하나를 { event, data } 로 파싱한다. 알 수 없는 필드는 버린다. */
function parseFrame(raw: string): { event: string; data: string } | null {
  let event = "message";
  const dataLines: string[] = [];

  for (const line of raw.split("\n")) {
    if (line.startsWith(":")) continue; // 주석 (하트비트)
    const colon = line.indexOf(":");
    const field = colon === -1 ? line : line.slice(0, colon);
    const value = colon === -1 ? "" : line.slice(colon + 1).replace(/^ /, "");

    if (field === "event") event = value;
    else if (field === "data") dataLines.push(value);
  }

  if (dataLines.length === 0) return null;
  return { event, data: dataLines.join("\n") };
}

/**
 * run 이벤트를 도착 순서대로 흘려보낸다. done 또는 failed 가 오면 스트림이 닫힌다.
 *
 * @example
 * const controller = new AbortController();
 * for await (const e of streamRunEvents(runId, controller.signal)) {
 *   if (e.type === "saved") appendObservations(e.data.observations);
 * }
 */
export async function* streamRunEvents(
  runId: string,
  signal?: AbortSignal,
): AsyncGenerator<RunEvent> {
  let response: Response;
  try {
    response = await fetch(buildUrl(`/runs/${runId}/events`), {
      headers: authHeaders({ Accept: "text/event-stream" }),
      signal,
      cache: "no-store",
    });
  } catch (cause) {
    if (cause instanceof DOMException && cause.name === "AbortError") throw cause;
    throw new NetworkError(cause);
  }

  if (!response.ok || !response.body) {
    throw new NetworkError(new Error(`SSE 연결 실패 (${response.status})`));
  }

  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";

  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += value;

      // 프레임 구분자는 빈 줄. \r\n 도 받아준다.
      let boundary: number;
      while ((boundary = buffer.search(/\r?\n\r?\n/)) !== -1) {
        const raw = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary).replace(/^\r?\n\r?\n/, "");

        const frame = parseFrame(raw);
        if (!frame) continue;

        yield { type: frame.event, data: JSON.parse(frame.data) } as RunEvent;

        if (frame.event === "done" || frame.event === "failed") return;
      }
    }
  } finally {
    reader.cancel().catch(() => {});
  }
}
