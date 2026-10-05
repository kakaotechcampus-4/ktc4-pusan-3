import { http, HttpResponse } from "msw";

import type { Agent, Observation } from "@/lib/api/types";

import { healthObservation, observations, runEventDrafts } from "../fixtures";
import { currentScenario } from "../scenario";
import { apiError, networkDelay, url } from "./helpers";
import { forgetRun, withIdempotency } from "./idempotency";
import { isPhotoRun, photoRunScript } from "./photos";

/**
 * 04 저장 결과 — 입력 한 줄과 run 스트림.
 *
 * 🚨 실패는 200 + 빈 배열이 아니다. failed 이벤트가 raw_text 를 돌려주고,
 *    화면은 그걸 입력창에 그대로 남긴다. 그래서 여기서 원문을 들고 있어야 한다.
 */

/**
 * 목 전용 아주 작은 상태. run_id → 그 run 을 만든 입력.
 *
 * `reply_to` 까지 들고 있는 이유는 **되묻기 답이 원문을 다시 보내지 않는지**를 테스트가 볼 수
 * 있어야 해서다 (#158 리뷰). 서버가 그 필드로 이전 run 을 찾을 예정이라, 목은 받아 두기만 한다.
 */
interface SubmittedInput {
  childId: string;
  text: string;
  replyTo?: string;
}

const inputByRun = new Map<string, SubmittedInput>();

/**
 * 되물은 run → 그 질문이 어느 아이 것인가. 서버의 pending store 를 흉내 낸다
 * (`apps/api/app/api/runs/pending_reply.py` · #175).
 *
 * - 질문을 내보내기 **직전에** 넣는다 — 서버도 `done` 앞에 넣어서, `note` 를 받자마자 보낸 답도 찾는다
 * - 답이 오면 **한 번만** 꺼낸다 — 같은 질문에 두 번 답하면 관찰이 두 행이 된다
 * - 이어받기 run 이 `failed` 로 끝나면 되돌려 둔다 — 같은 답으로 다시 시도할 수 있어야 한다
 * - 15분 만료 · 재시작은 흉내 내지 않는다. 그 400 은 `reply_unavailable` 시나리오가 만든다
 */
const pendingReplies = new Map<string, string>();

/**
 * `reply_failed` 에서 이미 한 번 실패시킨 질문. 🚨 **한 번만 실패시킨다** — 늘 실패하면
 * "다시 시도가 같은 `reply_to` 를 실었는가" 를 화면에서 볼 수 없다 (실으면 저장되고,
 * 빠뜨리면 맥락 없는 새 입력이라 질문이 또 뜬다).
 */
const failedOnce = new Set<string>();

/**
 * `reply_asks_again` 에서 이미 한 번 되물은 대화의 **처음 질문**. 🚨 **한 번만 더 묻는다** —
 * 늘 물으면 끝나지 않는다. 서버에도 재질문 상한이 들어올 예정이다(그 문구의 목은 서버 PR 뒤).
 */
const askedAgain = new Set<string>();

/** 테스트가 "답이 어느 run 을 가리키는가" 를 확인하는 자리. */
export function submittedInput(runId: string): SubmittedInput | undefined {
  return inputByRun.get(runId);
}

/** 목은 프로세스 수명만큼 산다 — 테스트 사이에 비운다 (`src/test/setup.ts`). */
export function resetSubmittedInputs(): void {
  inputByRun.clear();
  pendingReplies.clear();
  failedOnce.clear();
  askedAgain.clear();
}

/**
 * 없는 run · 다른 아이 · 이미 답한 질문을 하나로 합친다 — 서버와 같은 한 코드다 (#175).
 * 🚨 **문구는 상황만 말한다** (서버 `routers/children.py` 와 같은 글자). 무엇을 다시 보낼지는
 *    화면(`SubmitErrorCard`)이 이 아래에 적는다 — 여기서 "다시 적어 주세요" 를 말하면 바로 아래의
 *    "앞서 저장된 이야기는 다시 적지 않아도 돼요" 와 한 카드에서 부딪친다 (#208).
 */
function replyUnavailable() {
  return apiError(400, "reply_context_unavailable", "이전 질문을 이어서 확인할 수 없어요.");
}

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
/** 되묻는 질문. `note_question`(질문만) · `note_mixed`(저장 + 질문) 가 **같은 것**을 쓴다. */
const NOTE_QUESTION = "언제부터 그랬는지 알려주시겠어요?";

const SAFETY_GUIDANCE = {
  code: "safety_record",
  message: "알레르기·건강 정보는 직접 입력해 주세요. 대신 등록해 드릴 수 없어요.",
  deeplink: "settings/health-safety",
} as const;

/** 이어받기 run 이 저장한 뒤 Memory 가 하는 말. 🚨 질문이 아니다 — 이어받은 답은 또 묻지 않는다. */
const CONTINUATION_NOTE = "말씀해 주신 시점까지 함께 기록해 두었어요.";

/** `reply_asks_again` — 이어받기 run 이 한 번 더 묻는 말. 처음 질문과 **다른 문장**이라 화면에서 갈린다. */
const FOLLOW_UP_QUESTION = "하루에 몇 번 정도 그랬는지 알려주시겠어요?";

/**
 * 되묻기에 대한 답을 이어받는 run (#175). 🚨 **Supervisor 를 다시 타지 않는다** — 서버는 Memory 만
 * 남겨 둔 조각에 답을 붙여 저장한다. 그래서 단계가 하나고 추천 제안(`offer`)도 없다
 * (`apps/api/app/agents/pipeline.py` 의 `_handle_continuation`).
 */
async function* continuationScript(
  runId: string,
  input: SubmittedInput & { replyTo: string },
): AsyncGenerator<Uint8Array> {
  yield frame("step", { index: 1, total: 1, label: "이어서 적은 내용을 살펴보고 있어요" });
  await sleep(600);

  if (currentScenario() === "reply_failed" && !failedOnce.has(input.replyTo)) {
    failedOnce.add(input.replyTo);
    // 🚨 `failed` 보다 **먼저** 되돌린다. 화면이 받자마자 다시 시도해도 맥락과 키가 이미 풀려 있다.
    pendingReplies.set(input.replyTo, input.childId);
    forgetRun(runId);
    yield frame("failed", { reason: "internal_error", raw_text: input.text });
    return;
  }

  /**
   * 🚨 **이어받은 답이 또 모자라면 또 묻는다** (#226). 서버는 이 run 을 새 대기 자리에 넣으므로
   *    다음 답의 `reply_to` 는 **이 run** 이다 — 처음 질문의 run 은 위 핸들러가 이미 한 번 꺼냈다.
   *    어느 질문의 답인지는 처음 질문(`rootQuestion`)으로 센다 — 한 대화에서 한 번만 더 묻는다.
   */
  const rootQuestion = rootOf(input.replyTo);
  if (currentScenario() === "reply_asks_again" && !askedAgain.has(rootQuestion)) {
    askedAgain.add(rootQuestion);
    pendingReplies.set(runId, input.childId);
    yield frame("note", { text: FOLLOW_UP_QUESTION, kind: "question" });
    await sleep(300);
    yield frame("done", { run_id: runId, model_calls: 1 });
    return;
  }

  yield frame("saved", { observations: [healthObservation] });
  await sleep(300);
  /**
   * 🚨 **저장 뒤에 말이 한 번 더 온다** (#208). 서버는 이어받기에서 조기 종료를 껐다 — 답에 다른
   *    말이 섞였는지(leftover) 받을 턴이 있어야 해서다 (`apps/api/app/agents/memory/agent.py`).
   *    그래서 저장한 뒤 Memory 가 한 번 더 말하고 그게 `note` 로 나간다. `kind` 가 `message` 라
   *    화면은 "이어서 적기" 를 열지 않는다 — 열면 끝난 답에 또 답을 재촉한다.
   */
  yield frame("note", { text: CONTINUATION_NOTE, kind: "message" });
  await sleep(300);
  yield frame("done", { run_id: runId, model_calls: 1 });
}

/** 이어받기가 이어진 대화의 처음 run. 답의 답을 따라 처음 질문까지 거슬러 올라간다. */
function rootOf(runId: string): string {
  let current = runId;
  for (let replyTo = inputByRun.get(current)?.replyTo; replyTo;) {
    current = replyTo;
    replyTo = inputByRun.get(current)?.replyTo;
  }
  return current;
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
  const input = inputByRun.get(runId);
  const rawText = input?.text ?? "";

  // 🚨 이어받기 run 은 Supervisor 를 타지 않는다 (#175) — 의도 분류도, 일정 초안도 없다.
  if (input?.replyTo !== undefined) {
    yield* continuationScript(runId, { ...input, replyTo: input.replyTo });
    return;
  }

  const intent = intentOf(rawText);

  yield frame("step", { index: 1, total: 3, label: "무슨 말인지 보고 있어요" });
  await sleep(600);

  if (scenario === "failed") {
    // 여기서 스트림이 끝난다. 저장된 게 없으니 다음 칸으로 전파하지 않는다.
    forgetRun(runId);
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
  if (
    scenario === "note_question" ||
    scenario === "reply_failed" ||
    scenario === "reply_asks_again" ||
    scenario === "reply_unavailable"
  ) {
    if (input) pendingReplies.set(runId, input.childId);
    yield frame("note", { text: NOTE_QUESTION, kind: "question" });
    await sleep(300);
    yield frame("done", { run_id: runId, model_calls: 1 });
    return;
  }

  /**
   * 🚨 **일부는 저장되고 질문이 같이 오는 run** (#158 리뷰). Memory 가 한 후보를 저장한 뒤
   *    다른 후보의 정보가 모자라면 글로 되묻는다 — `Saved` 와 `MemoryNote` 가 한 run 에 같이
   *    나간다 (`apps/api/app/agents/pipeline.py`).
   *
   *    이 대본이 지키는 것: 답을 보낼 때 화면이 **원문을 다시 보내지 않는다.** 되돌려 이어
   *    적게 하면 위의 저장된 조각이 두 번 저장되고, 7일 승격 집계가 한 번의 관찰을 두 번으로
   *    센다 (최상위 §2 · #154). 맥락은 `reply_to` 로 서버가 찾는다.
   */
  if (scenario === "note_mixed") {
    yield frame("step", { index: 2, total: 3, label: "관찰을 나누고 있어요" });
    await sleep(500);
    yield frame("saved", { observations: [observations[0]] });
    await sleep(300);
    if (input) pendingReplies.set(runId, input.childId);
    yield frame("note", { text: NOTE_QUESTION, kind: "question" });
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
    withIdempotency(async ({ request, params }) => {
      await networkDelay();
      const childId = String(params.cid);
      const body = (await request.json()) as { text: string; reply_to?: string };

      // 🚨 한도보다 **먼저 확인만** 한다 — 잘못된 `reply_to` 가 하루 횟수를 쓰면 안 된다.
      //    꺼내는(지우는) 것은 한도를 통과한 뒤다. 한도에 걸린 답이 맥락까지 잃으면 안 되기
      //    때문이다 (#175 리뷰에서 서버에도 이 순서를 요청했다).
      // `reply_unavailable` 은 방금 물었어도 못 찾는다 — 15분 만료 · 서버 재시작을 흉내 낸다.
      if (
        body.reply_to !== undefined &&
        (currentScenario() === "reply_unavailable" || pendingReplies.get(body.reply_to) !== childId)
      ) {
        return replyUnavailable();
      }

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

      if (body.reply_to !== undefined) pendingReplies.delete(body.reply_to);

      const runId = `r_${Date.now()}`;
      // 🚨 `reply_to` 는 그대로 받아 둔다 — 이어받기 run 을 가르는 값이고 (#175),
      //    테스트가 "화면이 원문 대신 이 값을 보냈는가" 를 확인하는 자리다.
      inputByRun.set(runId, { childId, text: body.text, replyTo: body.reply_to });
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
