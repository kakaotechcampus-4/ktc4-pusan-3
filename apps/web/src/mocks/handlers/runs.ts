import { http, HttpResponse } from "msw";

import type { Agent, Observation } from "@/lib/api/types";

import { healthObservation, observations } from "../fixtures";
import { currentScenario } from "../scenario";
import { networkDelay, url } from "./helpers";

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
  http.post(url("/children/:cid/inputs"), async ({ request }) => {
    await networkDelay();
    const body = (await request.json()) as { text: string };
    const runId = `r_${Date.now()}`;
    inputTextByRun.set(runId, body.text);
    // 즉시 202 로 run_id 만 준다. 결과는 전부 SSE 로 흐른다.
    return HttpResponse.json({ run_id: runId }, { status: 202 });
  }),

  http.get(url("/runs/:runId/events"), ({ params }) => {
    const runId = String(params.runId);

    const stream = new ReadableStream<Uint8Array>({
      async start(controller) {
        try {
          for await (const chunk of runScript(runId)) {
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
