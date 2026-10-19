import type { AgentReadout, AgentResultEvent, PartialEvent } from "@/lib/api/sse";

/**
 * 04 답 묶음에 무엇을 세울지 고른다 (#227). 화면(`components/run-result.tsx`)은 그리기만 한다.
 */

type Place = { childId: string; runId: string | null };

/**
 * 화면에 세울 Agent 결과만 고른다. 🚨 **순서는 건드리지 않는다** — 도착한 순서가 실제로 끝난 순서다.
 *
 * 쓰는 task(급식 갱신)처럼 보일 것이 없는 결과는 빈 블록이 되므로 뺀다. 빈 블록을 세우면 Agent 이름만 있는
 * 카드가 "아무것도 못 했다" 로 읽힌다 — 실제로는 할 일을 끝낸 것이다. 추천만 있는 결과는 보일 것이 있다.
 */
export function visibleAgentResults(results: AgentResultEvent[]): AgentResultEvent[] {
  return results.filter(
    (result) =>
      result.suggestion_ids.length > 0 ||
      result.readouts.some((readout) => readout.body.trim() !== "") ||
      (result.question ?? "").trim() !== "",
  );
}

/**
 * 블록에 그릴 readout. 🚨 **후보가 있으면 개수 안내(`code` 가 붙은 것)는 그리지 않는다** — "후보 N개" 가
 * 대신한다. 날씨 안내처럼 `code` 가 없는 readout 은 개수와 상관없어서 그대로 그린다.
 */
export function blockReadouts(result: AgentResultEvent): AgentReadout[] {
  const hasCandidates = result.suggestion_ids.length > 0;
  return result.readouts.filter(
    (readout) => readout.body.trim() !== "" && !(hasCandidates && readout.code !== null),
  );
}

export interface BlockCandidates {
  count: number;
  /** 서버 코드 문구. 비어 오면 그리지 않는다. */
  reason: string | null;
  general: boolean;
  /** 일반 묶음의 쌓인 기록 수. 🚨 0 도 그린다. 서버가 세지 못했으면 `null` 이고 라벨만 선다. */
  recordCount: number | null;
  /**
   * 그 블록의 Agent · 그 run 의 05. run 을 모르거나 고를 수 있는 후보가 남지 않았으면 `null` — 링크를 그리지 않는다.
   */
  href: string | null;
  /**
   * 지난 대화에서 고를 수 있는 후보가 하나도 남지 않았다 (#228). 링크 대신 그 사실을 한 줄로 말한다.
   * 실시간 run 에서는 늘 `false` 다.
   */
  closed: boolean;
}

/**
 * 블록의 후보 묶음. 🚨 **지난 대화는 지금 상태로 가른다** (`AgentResultEvent.suggestions` · #228 리뷰).
 * `draft`(아직 고를 수 있다)나 `approved`(05 에서 보인다)가 하나라도 있어야 "후보 보기" 가 선다 — 다 지났거나
 * 거절했으면 눌러도 05 가 "이어서 할 수 없어요" 만 낸다. 실시간 run 은 상태가 오지 않고 방금 만든 것이라 늘 연다.
 */
export function blockCandidates(result: AgentResultEvent, place: Place): BlockCandidates | null {
  if (result.suggestion_ids.length === 0) return null;
  const closed =
    result.suggestions !== undefined &&
    !result.suggestions.some((s) => s.status === "draft" || s.status === "approved");
  return {
    count: result.suggestion_ids.length,
    reason: result.reason,
    general: result.kind === "general",
    recordCount: result.record_count,
    href: closed ? null : suggestionsHref([result.agent], place),
    closed,
  };
}

/**
 * 부분 실패 카드의 "다시 준비하기" (#227 3단계 · #249 판단 순서 ③). 🚨 **서버가 고른 Agent 만** 싣는다
 * (`PartialEvent.retryable`) — 화면이 `failed` 에서 고르면 급식 갱신만 실패한 식사로 05 를 열게 된다.
 */
export function partialRetryHref(partial: PartialEvent, place: Place): string | null {
  return partial.retryable.length > 0 ? suggestionsHref(partial.retryable, place) : null;
}

function suggestionsHref(agents: string[], place: Place): string | null {
  if (!place.runId) return null;
  const params = new URLSearchParams({ agents: agents.join(","), run: place.runId });
  return `/child/${place.childId}/suggestions?${params.toString()}`;
}
