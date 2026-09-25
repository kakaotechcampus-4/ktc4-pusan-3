import { http, HttpResponse } from "msw";

import type { Agent, Observation } from "@/lib/api/types";

import { healthObservation, observations, runEventDrafts } from "../fixtures";
import { currentScenario } from "../scenario";
import { networkDelay, url } from "./helpers";
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
 * 🚨 **한 줄이 무엇을 만드는지는 발화에 달려 있다** (CLAUDE.md §5 의도 3형).
 *    목이 늘 관찰과 초안을 **둘 다** 내면 두 화면을 따로 볼 수가 없다 — 기록만 남는 한 줄과
 *    일정이 되는 한 줄은 04 에서 다르게 생겼는데, 그 차이를 확인할 방법이 없었다.
 *
 * 🚨 **의도 분류를 흉내 내는 것이 아니다.** 목은 낱말만 본다 — 진짜 판정은 Supervisor 의 일이고
 *    (§3 — 의도 분류는 LLM), 여기서 규칙을 정교하게 만들면 **검증한 적 없는 분류기**를
 *    화면 개발의 기준으로 삼게 된다. 무엇을 치면 무엇이 나오는지는
 *    [`docs/web/mock-screens-v1.md`](../../../../docs/web/mock-screens-v1.md) 에 적어 둔다.
 */
const EVENT_WORDS = ["일정", "약속", "예약", "가기", "잡아", "등원", "소풍", "운동회", "물놀이"];
const RECORD_WORDS = ["했대", "했어", "놀았", "먹었", "좋아해", "싫어해", "그렸", "봤대"];

type Intent = "record" | "event" | "both";

function intentOf(text: string): Intent {
  const hasEvent = EVENT_WORDS.some((word) => text.includes(word));
  const hasRecord = RECORD_WORDS.some((word) => text.includes(word));
  if (hasEvent && !hasRecord) return "event";
  if (hasRecord && !hasEvent) return "record";
  // 🚨 아무것도 안 걸리면 **혼합형**이다 — 아무 한 줄이나 쳐 본 사람이 빈 화면을 보지 않게.
  return "both";
}

/** 실제 파이프라인 순서다 — saved 는 promoted 보다 항상 먼저 온다 (CLAUDE.md §4). */
async function* runScript(runId: string): AsyncGenerator<Uint8Array> {
  const scenario = currentScenario();
  const rawText = inputTextByRun.get(runId) ?? "";
  const intent = intentOf(rawText);

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

  yield frame("step", { index: 2, total: 3, label: "관찰을 나누고 있어요" });
  await sleep(700);

  /**
   * 🚨 **일정만 말한 한 줄에는 관찰이 없다.** "이번 주말에 공원 산책 가기" 는 아이가 무엇을
   *    했다는 말이 아니라 앞으로의 계획이라, 기록으로 쌓을 것이 없다 — 화면은 그때
   *    "아이에 관한 기록은 찾지 못했어요" 를 그리고 아래에 초안을 세운다.
   */
  if (intent !== "event") {
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
  }

  /**
   * 🚨 **한 프레임에 배열이다** (#122 — `EventDrafts` 는 run 당 한 번). 초안마다 한 프레임이면
   *    화면이 묶음을 한 번에 못 그린다.
   * 🚨 **아무것도 저장되지 않았다.** 저장은 보호자가 카드에서 제출할 때 한 번이다 (게이트 ㉠).
   * 🚨 한 run 이 `create` 와 `update` 를 **같이** 낼 수 있다 ("금요일에 물놀이 있어. 그리고
   *    운동회는 5시로 옮겨줘"). 화면이 op 로 엔드포인트를 가르는지 여기서 확인된다.
   * ⚠️ 프레임 이름이 계약에 없다 — `sse.ts` 의 `DRAFT_EVENT_NAMES` 참고 (#151).
   */
  if (intent !== "record") {
    // 🚨 `event` 는 새 일정 한 장만, `both` 는 create·update 두 장이다 — 한 장일 때와
    //    여러 장일 때 화면이 다르다(넘기는 줄이 서는가). 둘 다 확인할 수 있어야 한다.
    yield frame("event_draft", {
      drafts: intent === "event" ? runEventDrafts.slice(0, 1) : runEventDrafts,
    });
    await sleep(300);
  }

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
  // 🚨 키가 없으면 400 이다 — 같은 한 줄이 관찰 N건씩 두 번 저장되는 것을 막는 지점.
  http.post(
    url("/children/:cid/inputs"),
    withIdempotency(async ({ request }) => {
      await networkDelay();
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
