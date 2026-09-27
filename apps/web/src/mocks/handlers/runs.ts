import { http, HttpResponse } from "msw";

import type { Agent, Observation } from "@/lib/api/types";

import { healthObservation, observations } from "../fixtures";
import { currentScenario } from "../scenario";
import { apiError, networkDelay, url } from "./helpers";
import { withIdempotency } from "./idempotency";
import { isPhotoRun, photoRunScript } from "./photos";

/**
 * 04 저장 결과 — 입력 한 줄과 run 스트림.
 *
 * 🚨 실패는 200 + 빈 배열이 아니다. failed 이벤트가 raw_text 를 돌려주고,
 *    화면은 그걸 입력창에 그대로 남긴다. 그래서 여기서 원문을 들고 있어야 한다.
 */

/** 목 전용 아주 작은 상태. run_id → 그 run 을 만든 입력 원문. */
const inputTextByRun = new Map<string, string>();

function frame(event: string, data: unknown): Uint8Array {
  return new TextEncoder().encode(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`);
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/**
 * 안내 한 건. `guidance`(안내만) · `guidance_mixed`(안내 + 저장) 가 **같은 것**을 쓴다 —
 * 두 벌로 두면 한쪽만 고쳐져서 화면이 두 경우를 다르게 그리는지 알 수 없다.
 */
const SAFETY_GUIDANCE = {
  code: "safety_record",
  message: "알레르기·건강 정보는 직접 입력해 주세요. 대신 등록해 드릴 수 없어요.",
  deeplink: "settings/health-safety",
} as const;

/** 실제 파이프라인 순서다 — saved 는 promoted 보다 항상 먼저 온다 (CLAUDE.md §4). */
async function* runScript(runId: string): AsyncGenerator<Uint8Array> {
  const scenario = currentScenario();
  const rawText = inputTextByRun.get(runId) ?? "";

  yield frame("step", { index: 1, total: 3, label: "무슨 말인지 보고 있어요" });
  await sleep(600);

  if (scenario === "failed") {
    // 여기서 스트림이 끝난다. 저장된 게 없으니 다음 칸으로 전파하지 않는다.
    yield frame("failed", { reason: "unparsable", raw_text: rawText });
    return;
  }

  if (scenario === "disconnected") {
    // 🚨 done·failed 없이 그냥 닫힌다 (서버·프록시가 종료 이벤트 전에 끊은 경우).
    //    화면이 진행 상태에 영원히 남지 않는지 확인할 방법이 이것뿐이다 (PR #71 리뷰).
    return;
  }

  /**
   * 🚨 **모든 대본에 하나씩 넣는다** (#140 · #141). Supervisor 가 생각하는 동안 채널이 조용해서
   *    실제로 여기쯤 온다. 화면이 할 일은 없지만, **아무 일도 일어나지 않는지**를 확인할 자리가
   *    있어야 한다 — 모르는 이벤트 하나가 04 를 다시 그리거나 상태를 흔들면 여기서 드러난다.
   */
  yield frame("ping", {});
  await sleep(200);

  /**
   * 🚨 **안내만 나가고 끝나는 run 이다.** 원문이 전부 안내 조각이면 서버는 Memory 를 아예
   *    건너뛴다 (`apps/api/app/agents/pipeline.py`) — 그래서 `saved` 도 `promoted` 도 없다.
   *    화면이 이때 "아이에 관한 기록은 찾지 못했어요" 를 띄우면 **안내와 같은 자리에서 서로 다른
   *    이유**를 대게 된다 (`components/run-result.tsx` 의 `noticedWhy`).
   */
  if (scenario === "guidance") {
    yield frame("guidance", SAFETY_GUIDANCE);
    await sleep(300);
    yield frame("done", { run_id: runId, model_calls: 1 });
    return;
  }

  /**
   * 🚨 **한 줄에 두 얘기가 섞인 경우다** ("계란 잘 먹었어. 그리고 땅콩 알레르기 있어").
   *    안내는 라우팅이 내고 Memory 는 **기록 조각만** 받으므로, 서버는 앞을 저장하면서 뒤를
   *    안내로 돌린다 (`apps/api/app/agents/pipeline.py`). 그래서 안내가 저장보다 먼저 나간다 —
   *    이 순서 그대로 둬야 "안내가 저장을 감추지 않는다" 를 확인할 수 있다.
   */
  if (scenario === "guidance_mixed") {
    yield frame("guidance", SAFETY_GUIDANCE);
    await sleep(300);
  }

  /**
   * 🚨 **되묻기 하나만 있는 run 이다.** Memory 가 tool 없이 답하고 끝나면 이것뿐이라
   *    (`apps/api/app/agents/memory/agent.py` — `final_message`), 화면에 자리가 없으면
   *    **내용 없는 "다 됐어요"** 가 뜬다. 그게 #141 이 시작된 이유다.
   */
  if (scenario === "note_question") {
    yield frame("note", { text: "언제부터 그랬는지 알려주시겠어요?", kind: "question" });
    await sleep(300);
    yield frame("done", { run_id: runId, model_calls: 1 });
    return;
  }

  /**
   * 🚨 **저장보다 먼저 온다.** 서버도 라우팅 직후, Memory 가 돌기 전에 보낸다 (`pipeline.py`) —
   *    혼합형 한 줄("계란 잘 먹었어. 놀이도 추천해줘")이면 아래 `saved` 와 같은 run 에 실린다.
   *    이 순서 그대로 둬야 "준비 중 안내가 저장을 가리지 않는다" 를 확인할 수 있다 (NF-06).
   */
  if (scenario === "unavailable") {
    yield frame("unavailable", { agents: ["activity"] satisfies Agent[] });
    await sleep(300);
  }

  yield frame("step", { index: 2, total: 3, label: "관찰을 나누고 있어요" });
  await sleep(700);

  const saved: Observation[] = [observations[0], healthObservation];
  yield frame("saved", { observations: saved });
  await sleep(400);

  yield frame("step", { index: 3, total: 3, label: "기억을 정리하고 있어요" });
  await sleep(500);

  yield frame("promoted", {
    changes: [
      {
        ref: { kind: "profile_affinity", id: "a_12" },
        merge_key: "계란 반찬",
        state_before: "candidate",
        state_after: "confirmed",
        state_reason: "서로 다른 3일에 관찰됐어요",
      },
    ],
  });
  await sleep(400);

  if (scenario === "partial") {
    // 🚨 실패 화면으로 떨어뜨리지 않는다. 성공한 쪽은 그대로 보여준다 (NF-06).
    const succeeded: Agent[] = ["food"];
    const failed: Agent[] = ["activity"];
    yield frame("partial", { reason: "timeout_20s", succeeded, failed });
    await sleep(200);
  } else if (scenario === "unavailable") {
    // 🚨 준비 중이라고 말한 Agent 를 바로 아래에서 권하지 않는다 — 화면이 자기 말을 뒤집는다.
    yield frame("offer", { options: [{ agent: "food", label: "오늘 저녁 같이 정하기" }] });
    await sleep(200);
  } else {
    yield frame("offer", {
      options: [
        { agent: "food", label: "오늘 저녁 같이 정하기" },
        { agent: "activity", label: "주말 놀이 정하기" },
      ],
    });
    await sleep(200);
  }

  yield frame("done", { run_id: runId, model_calls: 2 });
}

export const runHandlers = [
  // 🚨 키가 없으면 400 이다 — 같은 한 줄이 관찰 N건씩 두 번 저장되는 것을 막는 지점.
  http.post(
    url("/children/:cid/inputs"),
    withIdempotency(async ({ request }) => {
      await networkDelay();

      /**
       * 🚨 **재시도해도 계속 429 다** (#147). 래퍼가 4xx 를 저장하지 않아서(`isReplayable`)
       *    같은 키로 다시 보내면 이 핸들러가 다시 돌고 또 429 가 나간다 — 실제 서버와 같다.
       *    화면이 "다시 시도" 버튼을 세우면 안 되는 이유가 여기서 눈으로 확인된다.
       */
      if (currentScenario() === "daily_limit") {
        return apiError(
          429,
          "daily_input_limit",
          "오늘은 더 적을 수 없어요. 내일 다시 적어 주세요.",
        );
      }

      const body = (await request.json()) as { text: string };
      const runId = `r_${Date.now()}`;
      inputTextByRun.set(runId, body.text);
      // 즉시 202 로 run_id 만 준다. 결과는 전부 SSE 로 흐른다.
      return HttpResponse.json({ run_id: runId }, { status: 202 });
    }),
  ),

  /**
   * 🚨 **한 경로를 두 종류의 run 이 나눠 쓴다** (계약서 §09 "SSE 채널을 재사용한다").
   *    같은 경로에 핸들러를 두 개 등록하면 msw 가 먼저 등록된 쪽으로만 보내서, 사진 run 이
   *    한 줄 입력 대본을 받아 **저장한 적도 없는 관찰이 `saved` 로 흘러나온다.**
   *    갈라 쓰는 지점을 여기 한 곳에 둔다.
   */
  http.get(url("/runs/:runId/events"), ({ params }) => {
    const runId = String(params.runId);
    const script = isPhotoRun(runId) ? photoRunScript(runId) : runScript(runId);

    const stream = new ReadableStream<Uint8Array>({
      async start(controller) {
        try {
          for await (const chunk of script) {
            controller.enqueue(chunk);
          }
        } finally {
          controller.close();
        }
      },
    });

    return new HttpResponse(stream, {
      headers: {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
        Connection: "keep-alive",
      },
    });
  }),
];
