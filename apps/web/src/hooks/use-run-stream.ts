"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useReducer, useRef } from "react";

import { qk } from "@/lib/api/queryKeys";
import {
  isDraftEvent,
  streamRunEvents,
  type DoneEvent,
  type EventDraftsEvent,
  type FailedEvent,
  type GuidanceEvent,
  type LaneEvent,
  type NoteEvent,
  type OfferEvent,
  type ParsedEvent,
  type PartialEvent,
  type PromotedEvent,
  type RunEvent,
  type StepEvent,
  type UnavailableEvent,
} from "@/lib/api/sse";
import type { Agent, EventDraft, Observation } from "@/lib/api/types";

/**
 * 04 대화 · 08 사진 분석 — run 이벤트 스트림 상태.
 *
 * run 상태는 서버 상태(TanStack Query)도 클라이언트 상태(Zustand)도 아니다. 구독형이라
 * 둘 다 안 맞아서, 리듀서로 들고 끝날 때 Query 를 무효화한다. 리듀서와 구독(`subscribeRun`)은
 * 하나고, **누가 들고 있는가**만 갈린다 — 08 은 이 파일의 훅이, 04 대화는 `stores/conversation.ts`
 * 가 든다 (#226 — 대화는 화면을 떠나도 스트림을 끊지 않는다).
 *
 * 🚨 입력 원문(raw_text)을 run 상태에 두지 않는다. 실패 시 입력창에 원문을 되돌려야 하는데
 *    (CLAUDE.md §2), failed 이벤트의 raw_text 는 네트워크가 끊기면 못 받는다 —
 *    **원문의 정본은 보낸 쪽이 들고 있는 값이다** (대화는 그 한 줄의 말풍선).
 */

/** 스트림이 이 시간 동안 조용하면 기다리기를 멈추고 지금까지 받은 것으로 끝낸다. */
export const RUN_IDLE_TIMEOUT_MS = 20_000;

/**
 * run 의 끝.
 *
 * `done` · `partial` · `failed` 는 **서버가 말한 끝**이다. `unconfirmed` 하나만 다르다 —
 * 20초 침묵이나 종료 이벤트 없는 EOF 처럼 **클라이언트가 스스로 끝낸** 경우라, 서버가 무엇을
 * 저장했는지 모른다. 이 둘을 한 값으로 묶으면 "저장됐는지 모르는 상태" 가 "부분 성공" 으로
 * 보이고, 화면이 저장됐다고 말한 뒤 입력창의 원문까지 지운다 (PR #71 리뷰).
 */
export type RunStatus = "idle" | "streaming" | "done" | "partial" | "failed" | "unconfirmed";

export interface RunState {
  status: RunStatus;
  /** 진행 오버레이의 현재 단계. */
  step: StepEvent | null;
  observations: Observation[];
  promoted: PromotedEvent["changes"];
  offers: OfferEvent["options"];
  /**
   * 08 사진 — 이 사진을 어느 쪽으로 읽었는지 **추측**. 04 한 줄 입력 run 에서는 늘 `null` 이다.
   * 🚨 확정이 아니다. lane 의 정본은 부모가 시트에서 고른 값이고, 08 화면은 이 값을 쓰지 않는다.
   */
  lane: LaneEvent | null;
  /**
   * 08 사진 — 사진에서 읽어낸 것. 🚨 **아직 저장된 것이 아니다** — `commit` 을 눌러야 저장된다.
   */
  parsed: ParsedEvent | null;
  /**
   * 🚨 **서버가 보낸 것만 들어온다.** 채워져 있으면 성공·실패를 한 화면에 섞어 그린다 (NF-06).
   *    클라이언트가 스스로 끝낸 경우(`unconfirmed`)를 여기 넣지 않는다 — 어느 Agent 가 성공했는지
   *    모르는데 `failed: []` 를 채워 넣으면 화면이 빈 목록으로 "준비하지 못했어요" 를 그린다.
   */
  partial: PartialEvent | null;
  /**
   * "이건 대신 해 드릴 수 없어요" 안내 (#141). 🚨 **실패가 아니다** — 채워져 있어도 run 은
   *    그대로 끝나고, 같은 run 에서 저장된 기록과 **한 화면에** 선다.
   * 🚨 **같은 `code` 는 한 번만 담는다.** 서버도 재분배 때 새로 생긴 안내만 보내지만
   *    (`apps/api/app/agents/pipeline.py`), 같은 말을 두 장 세우는 것은 화면이 막는다.
   */
  guidance: GuidanceEvent[];
  /**
   * Memory 가 한 말 (#141). **run 당 최대 하나다** — Memory 는 한 run 에서 한 번만 돌고
   * 위임이 어긋나 다시 나눌 때도 다시 돌리지 않는다 (`pipeline.py`).
   */
  note: NoteEvent | null;
  /**
   * 아직 없는 Agent 로 간 요청 (#141). 🚨 **저장과 같이 올 수 있다** — 라우팅 직후 Memory 가
   *    돌기 전에 나가서, 혼합형 한 줄이면 `observations` 와 같은 run 에 실린다.
   * 🚨 **합쳐서 담는다.** 재분배로 한 번 더 올 수 있는데 덮어쓰면 처음 것이 사라진다.
   */
  unavailable: Agent[];
  /**
   * Agent 가 만든 일정 초안 묶음. 🚨 **아직 저장된 것이 아니다** — 보호자가 카드에서 제출해야
   *    캘린더에 들어간다 (승인 게이트 ㉠). 08 사진 run 에서는 늘 빈 배열이다.
   */
  drafts: EventDraft[];
  /**
   * 🚨 **서버가 `failed` 로 말한 실패만 들어온다.** 연결이 끊긴 경우는 여기 오지 않는다 —
   *    그건 `unconfirmed` 다. 둘을 섞으면 화면이 "아무것도 저장하지 않았어요" 라고 단정한다.
   */
  failure: FailedEvent | null;
  /**
   * **저장한 뒤에 끊긴 run** — `done` 이 `completed: false` 로 왔다 (#253 · #271).
   *
   * 🚨 **실패가 아니다.** 서버는 무언가 실제로 쓴 뒤 끊겼을 때만 이 길로 닫고 Idempotency 키와
   *    이어받기 맥락을 놓지 않는다. 그래서 `status` 는 그대로 `done`(또는 `partial`)이고
   *    `isRunConfirmed` 도 참이다 — 화면도 끝난 run 으로 정리한다(질문을 닫는다).
   * 🚨 **무엇이 저장됐는지는 모른다.** `observations` 가 비어 있어도 "저장하지 않았어요" 가 아니다.
   */
  interrupted: boolean;
}

export const initialRunState: RunState = {
  status: "idle",
  step: null,
  observations: [],
  promoted: [],
  offers: [],
  lane: null,
  parsed: null,
  partial: null,
  guidance: [],
  note: null,
  unavailable: [],
  drafts: [],
  failure: null,
  interrupted: false,
};

export type RunAction =
  | { type: "start" }
  | { type: "event"; event: RunEvent }
  | { type: "timeout" }
  /** 종료 이벤트(done · failed) 없이 스트림이 닫혔다. */
  | { type: "closed" }
  | { type: "error" }
  | { type: "reset" };

/** 순수 함수다. 훅 없이 이벤트 배열만 흘려서 검증할 수 있다. */
export function runReducer(state: RunState, action: RunAction): RunState {
  switch (action.type) {
    case "start":
      return { ...initialRunState, status: "streaming" };

    case "reset":
      return initialRunState;

    // 서버가 끝을 말하지 않고 끝난 두 경우다 — 20초 침묵(timeout)과 종료 이벤트 없는 EOF(closed).
    // 🚨 **둘 다 저장 여부를 모른다.** 그래서 partial(=서버가 말한 부분 성공)로 두지 않는다.
    //    지금까지 받은 것은 그대로 들고 있고, 화면이 "확인하지 못했어요" 를 그린다.
    // 🚨 closed 를 처리하지 않으면 status 가 streaming 에 남는다 — 스트림이 끝나 idle 타이머까지
    //    해제된 뒤라 20초 전환조차 돌지 않아 진행 화면이 영원히 돈다 (PR #71 리뷰).
    case "timeout":
    case "closed":
      if (state.status !== "streaming") return state;
      return { ...state, status: "unconfirmed" };

    // 🚨 연결 실패·프레임 파싱 실패도 **서버가 무엇을 저장했는지 모르는** 상태다. 입력 POST 는
    //    이미 202 를 받았고 서버는 처리 중이다 — "아무것도 저장하지 않았어요" 라고 말할 근거가 없다.
    case "error":
      if (state.status !== "streaming") return state;
      return { ...state, status: "unconfirmed" };

    case "event":
      switch (action.event.type) {
        case "step":
          return { ...state, step: action.event.data as StepEvent };

        // 08 사진 — 아래 둘은 **저장이 아니다.** 부모가 확인하고 commit 을 눌러야 저장된다.
        case "lane":
          return { ...state, lane: action.event.data as LaneEvent };

        case "parsed":
          return { ...state, parsed: action.event.data as ParsedEvent };

        case "saved":
          return {
            ...state,
            observations: [
              ...state.observations,
              ...(action.event.data as { observations: Observation[] }).observations,
            ],
          };

        case "promoted":
          return { ...state, promoted: (action.event.data as PromotedEvent).changes };

        case "offer":
          return { ...state, offers: (action.event.data as OfferEvent).options };

        // 🚨 아래 셋도 **끝이 아니다.** 스트림은 done 까지 이어진다 — 안내가 왔다고 실패로
        //    떨어뜨리면 같은 run 에서 저장된 기록이 화면에서 사라진다 (최상위 §2).
        case "guidance": {
          const guidance = action.event.data as GuidanceEvent;
          if (state.guidance.some((g) => g.code === guidance.code)) return state;
          return { ...state, guidance: [...state.guidance, guidance] };
        }

        // 🚨 마지막 것이 남는다. run 당 하나뿐이라(위 주석) 실제로는 덮이지 않는다.
        case "note":
          return { ...state, note: action.event.data as NoteEvent };

        case "unavailable": {
          const { agents } = action.event.data as UnavailableEvent;
          const added = agents.filter((agent) => !state.unavailable.includes(agent));
          if (added.length === 0) return state;
          return { ...state, unavailable: [...state.unavailable, ...added] };
        }

        // 🚨 **아무것도 하지 않는 것이 맞다** (#140). 조용한 채널의 하트비트라 담을 것이 없고,
        //    20초 타이머는 `pumpRunEvents` 의 `onEvent` 가 이미 되살렸다. 여기서 state 를 새로
        //    만들면 10초마다 04 화면이 통째로 다시 그려진다.
        case "ping":
          return state;

        case "partial":
          // 🚨 여기서 끝내지 않는다. 스트림은 done 까지 이어지고, 화면은 성공한 쪽을 그린다.
          return { ...state, partial: action.event.data as PartialEvent };

        case "failed":
          return { ...state, status: "failed", failure: action.event.data as FailedEvent };

        case "done":
          // partial 이 왔었다면 done 이 와도 "부분 결과"다. 성공 화면으로 덮지 않는다.
          // 🚨 `completed` 는 `false` 일 때만 끊김이다 — 정상 done 에는 키가 없다 (#271).
          return {
            ...state,
            status: state.partial ? "partial" : "done",
            interrupted: (action.event.data as DoneEvent).completed === false,
          };

        default:
          /**
           * 🚨 **초안 프레임은 이름이 확정되기 전이라 `isDraftEvent()` 로 받는다** (#151).
           *    `case` 로 박아 두면 이름이 바뀌는 날 초안이 조용히 안 그려진다.
           * 🚨 **덮어쓰지 않고 이어 붙인다** — 한 run 이 여러 프레임을 보낼 수 있고,
           *    `DraftBook` 이 같은 일정을 이미 한 장으로 합쳐 보낸다 (#122 §5-1).
           */
          if (isDraftEvent(action.event.type)) {
            const { drafts } = action.event.data as EventDraftsEvent;
            return { ...state, drafts: [...state.drafts, ...drafts] };
          }
          return state;
      }
  }
}

/**
 * 서버가 이 입력을 **끝까지 처리했다고 말한** 상태인가 — 입력창의 원문을 비워도 되는 기준이다.
 *
 * 🚨 `unconfirmed` 는 여기 들어오지 않는다. 저장됐는지 모르는 채로 원문을 지우면 보호자가 적은
 *    말이 아무 데도 남지 않는다 (apps/web/CLAUDE.md §3 · PR #71 리뷰).
 */
export function isRunConfirmed(status: RunStatus): boolean {
  return status === "done" || status === "partial";
}

/**
 * 스트림을 액션으로 옮긴다. 훅 밖에 두는 이유는 `runReducer` 와 같다 — 이벤트만 흘려서 검증한다.
 *
 * @param onEvent 이벤트가 올 때마다 부른다. 훅이 idle 타이머를 다시 거는 자리다.
 */
export async function pumpRunEvents(
  source: AsyncIterable<RunEvent>,
  dispatch: (action: RunAction) => void,
  onEvent: () => void = () => {},
): Promise<void> {
  let ended = false;

  for await (const event of source) {
    onEvent();
    dispatch({ type: "event", event });
    // 스트림을 닫는 이벤트 둘. sse.ts 의 generator 도 이 둘에서 return 한다.
    if (event.type === "done" || event.type === "failed") ended = true;
  }

  // 🚨 서버·프록시가 종료 이벤트 전에 연결을 닫으면 여기로 온다. 조용히 끝내면 진행 화면이 남는다.
  if (!ended) dispatch({ type: "closed" });
}

/**
 * run 하나를 구독한다 — 20초 침묵 · 종료 이벤트 없는 EOF · 연결 실패를 **한 곳에서** 액션으로 옮긴다.
 *
 * 08 사진(`useRunStream`)과 04 대화(`stores/conversation.ts`)가 같이 쓴다. 대화는 화면을 떠나도
 * 스트림을 끊지 않아야 해서(#226) 훅 밖에서 구독하는데, 타이머와 EOF 처리를 그쪽에 한 벌 더
 * 두면 한쪽만 고쳐진다 — PR #71 리뷰가 잡은 "진행 화면이 영원히 도는" 사고가 그 한쪽에서 다시 난다.
 *
 * @param onSettled 스트림이 어떻게 끝났든 마지막에 한 번 부른다 (끊은 경우 포함).
 * @returns 구독을 끊는 함수. 🚨 끊어도 `onSettled` 는 불린다.
 */
export function subscribeRun(
  runId: string,
  dispatch: (action: RunAction) => void,
  onSettled: () => void = () => {},
): () => void {
  const controller = new AbortController();
  let idleTimer: ReturnType<typeof setTimeout> | null = null;

  const clearIdleTimer = () => {
    if (idleTimer !== null) {
      clearTimeout(idleTimer);
      idleTimer = null;
    }
  };

  // 이벤트가 올 때마다 다시 건다 — 전체 시간이 아니라 "조용한 시간"을 잰다.
  const armIdleTimer = () => {
    clearIdleTimer();
    idleTimer = setTimeout(() => {
      dispatch({ type: "timeout" });
      controller.abort();
    }, RUN_IDLE_TIMEOUT_MS);
  };

  void (async () => {
    try {
      armIdleTimer();
      // 중단(abort)은 여기서 throw 로 나간다 — closed 가 아니라 아래 catch 로 간다.
      await pumpRunEvents(streamRunEvents(runId, controller.signal), dispatch, armIdleTimer);
    } catch (cause) {
      const aborted = cause instanceof DOMException && cause.name === "AbortError";
      if (!aborted) dispatch({ type: "error" });
    } finally {
      clearIdleTimer();
      onSettled();
    }
  })();

  return () => {
    clearIdleTimer();
    controller.abort();
  };
}

export interface UseRunStream {
  state: RunState;
  /** run_id 로 구독을 시작한다. */
  start: (runId: string) => void;
  stop: () => void;
  reset: () => void;
}

/**
 * 화면이 사는 동안만 run 하나를 구독한다 — **08 사진 전용**이다.
 *
 * 🚨 **04 한 줄 입력은 이 훅을 쓰지 않는다** (#226). 대화 화면을 떠났다 와도 결과가 빠짐없이
 *    그려져야 해서 구독이 화면보다 오래 산다 (`stores/conversation.ts`). 08 은 화면이 곧 흐름이라
 *    떠나면 끊는 쪽이 맞다 — 커밋 전에는 저장된 것이 없다.
 */
export function useRunStream(childId: string): UseRunStream {
  const [state, dispatch] = useReducer(runReducer, initialRunState);
  const queryClient = useQueryClient();

  const unsubscribeRef = useRef<(() => void) | null>(null);

  const stop = useCallback(() => {
    unsubscribeRef.current?.();
    unsubscribeRef.current = null;
  }, []);

  // 화면을 벗어나면 스트림을 끊는다.
  useEffect(() => stop, [stop]);

  const start = useCallback(
    (runId: string) => {
      stop();
      dispatch({ type: "start" });
      unsubscribeRef.current = subscribeRun(runId, dispatch, () => {
        // 🚨 run 하나가 홈·관찰·프로필을 동시에 바꾼다. 골라서 무효화하면 빠뜨린 쪽이 낡는다.
        void queryClient.invalidateQueries({ queryKey: qk.child(childId) });
      });
    },
    [childId, queryClient, stop],
  );

  const reset = useCallback(() => {
    stop();
    dispatch({ type: "reset" });
  }, [stop]);

  return { state, start, stop, reset };
}
