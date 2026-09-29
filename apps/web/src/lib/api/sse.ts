import { authHeaders, buildUrl } from "./client";
import { NetworkError } from "./errors";
import type { Affinity, Agent, EventDraft, Observation, PhotoEntry, PhotoLane, Ref } from "./types";

/**
 * GET /runs/{rid}/events — 04 오버레이.
 *
 * EventSource 를 쓰지 않는 이유: Authorization 헤더를 붙일 수 없다 (NF-09 는 예외를 두지 않는다).
 * 그래서 fetch + ReadableStream 으로 직접 프레임을 읽는다.
 *
 * 🚨 이벤트는 도착 순서대로 그린다. saved 는 promoted 보다 항상 먼저 온다 —
 *    저장이 검색보다 먼저이기 때문이다 (CLAUDE.md §4).
 *
 * 🚨 **08 사진 run 은 같은 채널을 쓰지만 다른 이벤트가 온다** (계약서 §09). `lane` · `parsed` 가
 *    추가되고, `saved` 는 **오지 않는다** — 사진은 승인(`commit`) 전에 아무것도 저장하지 않는다.
 *    한 스트림 구현을 둘이 나눠 쓰는 것이라, 화면이 자기가 어느 run 을 보고 있는지 알고 그린다.
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
 *
 * ⚠️ `reason` 을 화면이 갈라 그리지 않는다 — 부모가 할 수 있는 일이 넷 다 같다(다시 시도 ·
 *    고쳐 쓰기). 여기 적어 두는 것은 **서버가 보낼 수 있는 값을 알아보게** 하기 위해서다 (#141).
 */
export interface FailedEvent {
  reason:
    | "unparsable"
    | "no_child_observation"
    | "llm_unavailable"
    /** run 이 60초 안에 안 끝나 서버가 끊었다 (#147). */
    | "timeout"
    /** 서버 쪽 예외 (#147). */
    | "internal_error"
    | (string & {});
  raw_text: string;
}

/**
 * 04 한 줄 입력 — **"이건 대신 해 드릴 수 없어요" 안내** (#141).
 *
 * 🚨 **실패가 아니다.** 와도 run 은 그대로 이어지고, 여러 개가 `step` 과 `done` 사이 아무 때나 온다.
 *    실패 화면으로 떨어뜨리면 같은 run 에서 저장된 기록이 화면에서 사라진다.
 * 🚨 **`message` 를 화면이 다시 쓰지 않는다.** "알레르기는 직접 입력해 주세요" 는 정책 문장이라
 *    (최상위 §2) 화면이 `code` 를 보고 문구를 따로 만들면 정책과 화면이 두 곳에서 갈린다.
 *    화면이 `code` 로 정하는 것은 **아이콘과 버튼**뿐이다.
 *
 * `code` 를 열어 두는 이유는 `Guard.code` 와 같다 — 서버가 종류를 늘려도 `message` 는 그대로
 * 그려지므로 화면을 먼저 배포하지 않아도 된다.
 */
export interface GuidanceEvent {
  code:
    /** 알레르기·건강은 보호자가 직접 입력한다 (최상위 §2 — LLM 이 생성·추론·수정하지 않는다). */
    | "safety_record"
    /** 진단·영양제 추천은 하지 않는다 (최상위 §1 안 만드는 것). */
    | "diagnosis"
    /** 구매·예약 대행은 하지 않는다. */
    | "out_of_scope"
    | (string & {});
  message: string;
  /**
   * ⚠️ 계약서의 `deeplink` 와 같이 **아이를 안 담은 상대 경로**다 (`settings/health-safety`).
   * 🚨 주소로 쓰지 않는다 — 어느 구역인지 **힌트**로만 쓴다 (apps/web/CLAUDE.md §3).
   *    서버가 키는 늘 실어 보내고 값이 없으면 `null` 이다 (`apps/api/app/api/runs/translate.py`).
   */
  deeplink?: string | null;
}

/**
 * 04 한 줄 입력 — **Memory 가 한 말** (#141).
 *
 * 🚨 **run 당 최대 한 번이다.** Memory 는 한 run 에서 한 번만 돌고, 위임이 어긋나 다시 나눌 때도
 *    다시 돌리지 않는다 (`apps/api/app/agents/pipeline.py` — "Memory는 다시 돌리지 않는다").
 *
 * 🚨 **`kind` 가 없으면 `message` 로 본다.** 서버가 종류를 안 실어 주면 화면은 되묻는 질문인지
 *    알 수 없는데, 그때 "이어서 적기" 를 세우면 **요약 문장에도** 답을 재촉하게 된다. 모르면
 *    말하지 않는 쪽이 이 서비스의 기본값이다 (최상위 §2).
 *    👉 `kind` 는 #141 에서 요청한 값이다 — 서버가 실어 주기 전까지는 늘 안내 문장으로만 선다.
 */
export interface NoteEvent {
  text: string;
  /** `question` — 답을 기다리는 되묻기. `message` — 알려 주기만 하는 말. */
  kind?: "question" | "message";
}

/**
 * 04 한 줄 입력 — **아직 없는 Agent 로 간 요청** (#141).
 *
 * 🚨 **`guidance` 와 같은 카드로 그리지만 같은 이벤트가 아니다.** `guidance` 는 앞으로도 안 하는
 *    것(정책)이고 이건 아직 안 되는 것(상태)이다. 한 `code` 목록에 섞으면 둘이 같은 무게로 읽힌다.
 * 🚨 **저장과 같이 온다.** 라우팅 직후 Memory 가 돌기 **전에** 나가서(`pipeline.py`), 혼합형
 *    한 줄이면 `saved` 와 같은 run 에 실린다 — 이걸 실패 화면으로 빼면 방금 저장된 기록이
 *    화면에서 사라진다 (최상위 §2 — 성공과 실패를 한 화면에 섞는다).
 * 🚨 **문구는 화면이 만든다.** 서버가 주는 것은 Agent 이름뿐이고, "○○ 쪽은" 은 `domainLabel` 이
 *    이미 만들고 있다 (`partial` 과 같은 표를 쓴다).
 */
export interface UnavailableEvent {
  agents: Agent[];
}

/**
 * 08 사진 — 이 사진을 문서로 읽을지 활동 사진으로 읽을지. 🚨 **추측이다.**
 * 프론트가 이 값을 확정으로 쓰지 않는다 — 화면이 부모에게 한 번 되묻고, 부모가 바꾸면 그쪽이 정본이다.
 */
export interface LaneEvent {
  guess: PhotoLane;
  /** 0~1. 🚨 숫자를 화면에 그리지 않는다 — 부모가 할 수 있는 일이 아니다. 문구만 바꾼다. */
  confidence: number;
}

/**
 * 08 사진 — 사진에서 읽어낸 것.
 *
 * 🚨 **`raw_text` 는 외부 텍스트다.** OCR 로 들어온 기관 공지가 모델을 거쳐 화면에 닿는 경로라
 *    `dangerouslySetInnerHTML` 로 그리지 않는다 (apps/web/CLAUDE.md §4).
 * 🚨 **아직 아무것도 저장되지 않았다.** 저장은 `POST /photo-runs/{rid}/commit` 뿐이다.
 */
export interface ParsedEvent {
  raw_text: string;
  /**
   * 문서 lane 에서 읽어낸 **항목들**. 알림장 한 장에 일정이 여러 개, 식단표는 한 달치가 온다.
   * ⚠️ 계약서 v1 의 `extracted`(항목 하나)를 대신한다 — `PhotoEntry` 의 ⚠️ 참고.
   */
  entries?: PhotoEntry[];
  /** 활동 lane 에서 뽑아낸 태그. 🚨 `entries` 와 **다른 필드다** (모양도 저장 경로도 다르다). */
  tags?: string[];
}

/**
 * 04 한 줄 입력 — Agent 가 만든 **일정 초안 묶음.**
 *
 * 🚨 **한 프레임에 배열로 온다** (#122 — `EventDrafts` 는 run 당 한 번이다). 초안마다 한 프레임이
 *    아니라서 화면이 묶음을 한 번에 그린다.
 * 🚨 **아직 아무것도 저장되지 않았다.** `event` 테이블에는 보호자가 제출한 행만 들어간다 (#118) —
 *    저장은 초안 제출 하나뿐이고 그게 승인 게이트 ㉠ 이다.
 * 🚨 **초안은 서버에 남지 않는다** (9/21). 화면이 세션 스토리지에 들고 있고, 새로고침하면 사라진다.
 *
 * ⚠️ **이벤트 이름이 계약에 없다.** #122 에서 모양(`{ drafts: [...] }` 한 프레임)은 확정됐지만
 *    프레임 이름은 답이 오지 않았다 — 계약서 §03 의 목록에도 `event_draft` 가 없다.
 *    아래 `DRAFT_EVENT_NAMES` 에 후보를 적어 두고 **둘 다 받는다.** 확정되면 하나로 줄인다.
 *    👉 `apps/api` Owner 협의 대상 (최상위 CLAUDE.md §8 · #151).
 */
export interface EventDraftsEvent {
  drafts: EventDraft[];
}

/**
 * 🚨 이름이 확정될 때까지 **둘 다 받는다.** 틀린 쪽 하나만 걸어 두면 초안이 조용히 안 그려지고,
 *    화면에는 "아무 일도 없었던 것" 처럼 보인다 — 실패가 안 보이는 게 제일 나쁘다.
 */
export const DRAFT_EVENT_NAMES = ["event_draft", "event_drafts"] as const;

export function isDraftEvent(type: string): boolean {
  return (DRAFT_EVENT_NAMES as readonly string[]).includes(type);
}

/** model_calls 가 4 를 넘으면 서버 알람이다 (NF-01). Agent 진입 1회 + 재시도마다 +1 로 센다. */
export interface DoneEvent {
  run_id: string;
  model_calls: number;
}

/**
 * 채널이 조용할 때 10초마다 오는 빈 프레임 (#140 · #141). **화면이 할 일은 없다.**
 *
 * 🚨 `:` 주석이 아니라 **이벤트**인 이유 — `parseFrame` 이 `data:` 없는 프레임을 버려서
 *    주석으로는 `RUN_IDLE_TIMEOUT_MS`(20초) 타이머가 안 되살아난다. 추론 모델 호출이 20초를
 *    넘으면 화면이 `unconfirmed` 로 떨어진다.
 * 🚨 **타이머는 리듀서가 아니라 `pumpRunEvents` 의 `onEvent` 가 되살린다** — 프레임이 파싱돼서
 *    여기까지 온 것만으로 이미 충분하다.
 */
export type PingEvent = Record<string, never>;

export type RunEvent =
  | { type: "step"; data: StepEvent }
  | { type: "lane"; data: LaneEvent }
  | { type: "parsed"; data: ParsedEvent }
  | { type: "saved"; data: SavedEvent }
  | { type: "promoted"; data: PromotedEvent }
  | { type: "offer"; data: OfferEvent }
  | { type: "event_draft"; data: EventDraftsEvent }
  | { type: "event_drafts"; data: EventDraftsEvent }
  | { type: "guidance"; data: GuidanceEvent }
  | { type: "note"; data: NoteEvent }
  | { type: "unavailable"; data: UnavailableEvent }
  | { type: "ping"; data: PingEvent }
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
