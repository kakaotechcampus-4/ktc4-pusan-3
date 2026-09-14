"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useReducer, useRef } from "react";

import { qk } from "@/lib/api/queryKeys";
import {
  streamRunEvents,
  type FailedEvent,
  type OfferEvent,
  type PartialEvent,
  type PromotedEvent,
  type RunEvent,
  type StepEvent,
} from "@/lib/api/sse";
import type { Observation } from "@/lib/api/types";

/**
 * 04 저장 결과 — run 이벤트 스트림 상태.
 *
 * run 상태는 서버 상태(TanStack Query)도 클라이언트 상태(Zustand)도 아니다. 구독형이라
 * 둘 다 안 맞아서, 화면이 사는 동안만 useReducer 로 들고 끝날 때 Query 를 무효화한다.
 *
 * 🚨 입력 원문(raw_text)을 이 훅에 두지 않는다. 화면을 벗어나면 스트림을 끊는데,
 *    실패 시 입력창에 원문을 되돌려야 해서(CLAUDE.md §2) 원문이 훅과 함께 죽으면 안 된다.
 *    failed 이벤트가 raw_text 를 실어 주지만, 네트워크가 끊기면 그것도 못 받는다 —
 *    **원문의 정본은 입력 화면이 들고 있는 값이다.**
 */

/** 스트림이 이 시간 동안 조용하면 기다리기를 멈추고 지금까지 받은 것으로 끝낸다. */
export const RUN_IDLE_TIMEOUT_MS = 20_000;

export type RunStatus = "idle" | "streaming" | "done" | "partial" | "failed";

export interface RunState {
  status: RunStatus;
  /** 진행 오버레이의 현재 단계. */
  step: StepEvent | null;
  observations: Observation[];
  promoted: PromotedEvent["changes"];
  offers: OfferEvent["options"];
  /** 채워져 있으면 성공·실패를 한 화면에 섞어 그린다 (NF-06). */
  partial: PartialEvent | null;
  /** raw_text 가 빈 문자열일 수 있다 — 스트림이 끊긴 경우다. 위 주석 참고. */
  failure: FailedEvent | null;
}

export const initialRunState: RunState = {
  status: "idle",
  step: null,
  observations: [],
  promoted: [],
  offers: [],
  partial: null,
  failure: null,
};

export type RunAction =
  | { type: "start" }
  | { type: "event"; event: RunEvent }
  | { type: "timeout" }
  | { type: "error" }
  | { type: "reset" };

/** 순수 함수다. 훅 없이 이벤트 배열만 흘려서 검증할 수 있다. */
export function runReducer(state: RunState, action: RunAction): RunState {
  switch (action.type) {
    case "start":
      return { ...initialRunState, status: "streaming" };

    case "reset":
      return initialRunState;

    case "timeout":
      // 20초 전환의 정본은 서버가 보내는 partial 이다. 이건 스트림이 멎었을 때의 안전망이라
      // 어느 Agent 가 성공했는지 알 수 없다 — 지금까지 받은 것만 들고 끝낸다.
      if (state.status !== "streaming") return state;
      return {
        ...state,
        status: "partial",
        partial: state.partial ?? { reason: "client_idle_timeout", succeeded: [], failed: [] },
      };

    case "error":
      if (state.status !== "streaming") return state;
      return { ...state, status: "failed", failure: { reason: "stream_error", raw_text: "" } };

    case "event":
      switch (action.event.type) {
        case "step":
          return { ...state, step: action.event.data as StepEvent };

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

        case "partial":
          // 🚨 여기서 끝내지 않는다. 스트림은 done 까지 이어지고, 화면은 성공한 쪽을 그린다.
          return { ...state, partial: action.event.data as PartialEvent };

        case "failed":
          return { ...state, status: "failed", failure: action.event.data as FailedEvent };

        case "done":
          // partial 이 왔었다면 done 이 와도 "부분 결과"다. 성공 화면으로 덮지 않는다.
          return { ...state, status: state.partial ? "partial" : "done" };

        default:
          return state;
      }
  }
}

export interface UseRunStream {
  state: RunState;
  /** POST /children/{cid}/inputs 가 준 run_id 로 구독을 시작한다. */
  start: (runId: string) => void;
  stop: () => void;
  reset: () => void;
}

export function useRunStream(childId: string): UseRunStream {
  const [state, dispatch] = useReducer(runReducer, initialRunState);
  const queryClient = useQueryClient();

  const abortRef = useRef<AbortController | null>(null);
  const idleTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const clearIdleTimer = useCallback(() => {
    if (idleTimerRef.current !== null) {
      clearTimeout(idleTimerRef.current);
      idleTimerRef.current = null;
    }
  }, []);

  const stop = useCallback(() => {
    clearIdleTimer();
    abortRef.current?.abort();
    abortRef.current = null;
  }, [clearIdleTimer]);

  // 화면을 벗어나면 스트림을 끊는다. 원문은 이 훅 밖에 있어야 한다 (위 주석).
  useEffect(() => stop, [stop]);

  const start = useCallback(
    (runId: string) => {
      stop();
      dispatch({ type: "start" });

      const controller = new AbortController();
      abortRef.current = controller;

      // 이벤트가 올 때마다 다시 건다 — 전체 시간이 아니라 "조용한 시간"을 잰다.
      const armIdleTimer = () => {
        clearIdleTimer();
        idleTimerRef.current = setTimeout(() => {
          dispatch({ type: "timeout" });
          controller.abort();
        }, RUN_IDLE_TIMEOUT_MS);
      };

      void (async () => {
        try {
          armIdleTimer();
          for await (const event of streamRunEvents(runId, controller.signal)) {
            armIdleTimer();
            dispatch({ type: "event", event });
          }
        } catch (cause) {
          const aborted = cause instanceof DOMException && cause.name === "AbortError";
          if (!aborted) dispatch({ type: "error" });
        } finally {
          clearIdleTimer();
          // 🚨 run 하나가 홈·관찰·프로필을 동시에 바꾼다. 골라서 무효화하면 빠뜨린 쪽이 낡는다.
          void queryClient.invalidateQueries({ queryKey: qk.child(childId) });
        }
      })();
    },
    [childId, clearIdleTimer, queryClient, stop],
  );

  const reset = useCallback(() => {
    stop();
    dispatch({ type: "reset" });
  }, [stop]);

  return { state, start, stop, reset };
}
