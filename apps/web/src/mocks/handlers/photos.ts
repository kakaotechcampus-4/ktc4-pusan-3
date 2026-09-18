import { http, HttpResponse } from "msw";

import type {
  Observation,
  PhotoCommitRequest,
  PhotoCommitResponse,
  PhotoLane,
  PhotoReanalyzeRequest,
} from "@/lib/api/types";

import { CHILD_ID, daysAgo, draftEvent } from "../fixtures";
import { currentScenario } from "../scenario";
import { apiError, networkDelay, url } from "./helpers";
import { withIdempotency } from "./idempotency";

/**
 * 08 사진으로 적기 — 업로드 · 다시 읽기 · 저장.
 *
 * 🚨 **여기서 만드는 상태의 핵심은 "아직 저장되지 않았다" 다.** `POST /photos` 도
 *    `/reanalyze` 도 아무것도 쌓지 않는다 — `commit` 만 관찰을 만든다. 목이 그렇게 행동해야
 *    화면이 "승인 전 미저장" 을 지키는지 테스트로 걸 수 있다.
 *
 * 🚨 **SSE 는 `GET /runs/{rid}/events` 하나다** (계약서 §09 "SSE 채널을 재사용한다").
 *    그래서 이 파일은 핸들러를 따로 등록하지 않고, 사진 run 인지 아는 **등록부**와
 *    그 run 의 이벤트 대본만 내보낸다. `runs.ts` 가 한 핸들러 안에서 갈라 쓴다 —
 *    같은 경로에 핸들러 두 개를 두면 msw 가 먼저 등록된 쪽으로만 보낸다.
 *
 * 🚨 **픽스처에 실제 발화나 아이 정보를 넣지 않는다** (저장소가 public · 최상위 §9).
 */

/** 이 run 이 사진 run 인가. 값은 그 run 이 읽어낸 것이다. */
interface PhotoRun {
  lane: PhotoLane;
  confidence: number;
  raw_text: string;
  items: string[];
  when: string | null;
  /**
   * 업로드할 때 화면이 함께 보낸 날짜(`YYYY-MM-DD`). 활동 lane 의 `calendar_date` 가 이 값이다 —
   * ⚠️ commit 요청에는 날짜 필드가 없어서(계약서 §09) 서버가 run 에 들고 있어야 한다.
   */
  date: string | null;
  /** 못 읽은 사진. `failed` 로 끝난다. */
  unreadable: boolean;
}

const photoRuns = new Map<string, PhotoRun>();

/** 🚨 목은 프로세스 수명만큼 산다. 테스트 사이에 비운다 (`src/test/setup.ts`). */
export function resetPhotoRuns(): void {
  photoRuns.clear();
}

export function isPhotoRun(runId: string): boolean {
  return photoRuns.has(runId);
}

/**
 * 시나리오가 사진의 종류를 정한다. 실서버로는 "읽히는 알림장" 과 "안 읽히는 사진" 을
 * 마음대로 만들 수 없어서(찍어 봐야 안다) 여기서 손으로 켠다.
 */
function readPhoto(date: string | null): PhotoRun {
  const scenario = currentScenario();

  if (scenario === "photo_unreadable") {
    return {
      lane: "document",
      confidence: 0.3,
      raw_text: "",
      items: [],
      when: null,
      date,
      unreadable: true,
    };
  }

  if (scenario === "photo_activity") {
    return {
      lane: "activity",
      confidence: 0.81,
      raw_text: "",
      items: ["블록 쌓기", "실내 놀이", "오래 앉아 있기", "혼자 놀기"],
      when: null,
      date,
      unreadable: false,
    };
  }

  // 기본은 문서 lane 이다 — 08 화면을 여는 가장 흔한 이유가 알림장이다.
  return {
    lane: "document",
    confidence: 0.72,
    raw_text: "9월 넷째 주 알림장\n금요일 물놀이가 있어요. 준비물: 수건, 수영복, 여벌 옷",
    items: ["수건", "수영복", "여벌 옷"],
    when: daysAgo(-3),
    date,
    unreadable: false,
  };
}

function frame(event: string, data: unknown): Uint8Array {
  return new TextEncoder().encode(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`);
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/**
 * 사진 run 의 이벤트 대본.
 *
 * 🚨 **`saved` 가 없다.** 한 줄 입력 run 과 갈리는 지점이고, 이 파일이 지키는 규칙 그 자체다 —
 *    사진은 보호자가 `commit` 을 눌러야 저장된다.
 */
export async function* photoRunScript(runId: string): AsyncGenerator<Uint8Array> {
  const run = photoRuns.get(runId);
  if (!run) return;

  yield frame("step", { index: 1, total: 2, label: "사진에서 글자를 찾고 있어요" });
  await sleep(600);

  if (run.unreadable) {
    // 🚨 실패는 200 + 빈 배열이 아니다. 여기서 스트림이 끝나고 저장된 것은 없다.
    yield frame("failed", { reason: "unparsable", raw_text: "" });
    return;
  }

  yield frame("lane", { guess: run.lane, confidence: run.confidence });
  await sleep(300);

  yield frame("step", { index: 2, total: 2, label: "읽은 것을 정리하고 있어요" });
  await sleep(500);

  yield frame("parsed", {
    raw_text: run.raw_text,
    extracted: { items: run.items, when: run.when, all_day: run.when !== null },
  });
  await sleep(200);

  yield frame("done", { run_id: runId, model_calls: 1 });
}

/** 저장할 때 만드는 관찰 1건. `lane` 이 `kind` 와 `confidence_source` 를 함께 정한다. */
function observationFor(lane: PhotoLane, items: string[], when: string | null): Observation {
  const observedOn = when ?? daysAgo(0);

  if (lane === "document") {
    return {
      id: `o_photo_${Date.now()}`,
      child_id: CHILD_ID,
      kind: "observation_education",
      raw_text: `알림장에서 읽었어요. 준비물: ${items.join(", ")}`,
      subject: items[0] ?? "알림장",
      polarity: 0,
      strong_signals: [],
      // 🚨 문서에서 읽은 것은 institution_notice 로 고정이다 (계약서 §09).
      //    기관 공지가 보호자 발화로 들어가면 출처 추적이 끊긴다.
      confidence_source: "institution_notice",
      status: "active",
      observed_from: observedOn,
      observed_to: observedOn,
      observed_label: "오늘",
      // 🚨 문서 lane 은 프로필 승격 경로에 올리지 않는다 — affinity 를 붙이지 않는다.
      affinity: null,
      domain_fields: { items },
    };
  }

  return {
    id: `o_photo_${Date.now()}`,
    child_id: CHILD_ID,
    kind: "observation_activity",
    raw_text: `사진에서 고른 활동: ${items.join(", ")}`,
    subject: items[0] ?? "활동",
    polarity: 0,
    strong_signals: [],
    // 🚨 사진 태그는 보호자가 고른 것이라 parent_hearsay 다 (계약서 §09).
    confidence_source: "parent_hearsay",
    status: "active",
    observed_from: observedOn,
    observed_to: observedOn,
    observed_label: "오늘",
    // 🚨 사진 태그는 성향으로 확정하지 않는다 — affinity_id 를 붙이지 않는다 (계약서 §09).
    affinity: null,
    domain_fields: { tags: items },
  };
}

export const photoHandlers = [
  /**
   * 🚨 키가 없으면 400 이다 — 같은 사진이 두 번 읽히면 모델 호출이 두 배다 (NF-01).
   *    계약서 §01 의 5개 중 하나다.
   */
  http.post(
    url("/children/:cid/photos"),
    withIdempotency(async ({ request }) => {
      await networkDelay();

      const form = await request.formData();
      const file = form.get("file");
      // ⚠️ 필드 이름이 계약서에 없어서 화면과 목이 `file` 로 맞춰 뒀다 (operations.ts 의 ⚠️).
      //    목이 이걸 검사해야 이름이 어긋났을 때 조용히 통과하지 않는다.
      if (!file || typeof file === "string") {
        return apiError(422, "validation_failed", "사진 파일이 없어요", { field: "file" });
      }

      const formDate = form.get("date");
      const runId = `pr_${Date.now()}`;
      photoRuns.set(runId, readPhoto(typeof formDate === "string" ? formDate : null));
      return HttpResponse.json({ run_id: runId }, { status: 202 });
    }),
  ),

  /**
   * 힌트 한 줄로 다시 읽기 → **새 run_id**.
   * 🚨 힌트를 그대로 저장하지 않는다 (계약서 §09). 목은 읽어낸 항목에 하나를 더해 보여서
   *    "다시 읽었다" 가 화면에서 실제로 달라지는지 확인할 수 있게 한다.
   */
  http.post(url("/photo-runs/:rid/reanalyze"), async ({ request, params }) => {
    await networkDelay();

    const previous = photoRuns.get(String(params.rid));
    if (!previous) return apiError(404, "not_found", "다시 읽을 사진이 없어요");

    const body = (await request.json()) as PhotoReanalyzeRequest;
    const hint = body.hint_text.trim();
    if (hint.length === 0) {
      return apiError(422, "validation_failed", "무엇이 빠졌는지 알려주세요", {
        field: "hint_text",
      });
    }

    const runId = `pr_${Date.now()}_r`;
    photoRuns.set(runId, {
      ...previous,
      unreadable: false,
      confidence: Math.min(previous.confidence + 0.15, 0.95),
      // 🚨 힌트를 **항목으로 저장하는 것이 아니다.** 다시 읽은 결과가 달라졌다는 것을
      //    목이 흉내 내는 것뿐이고, 실제 문구는 서버 모델이 만든다.
      items: [...previous.items, hint],
    });

    return HttpResponse.json({ run_id: runId }, { status: 202 });
  }),

  /** 🚨 사진 흐름에서 **저장이 일어나는 유일한 지점**이다. */
  http.post(url("/photo-runs/:rid/commit"), async ({ request, params }) => {
    await networkDelay();

    const run = photoRuns.get(String(params.rid));
    if (!run) return apiError(404, "not_found", "저장할 사진이 없어요");

    const body = (await request.json()) as PhotoCommitRequest;

    // 고른 것도 없고 올릴 날짜도 없으면 저장할 내용이 없다. 빈 저장을 만들지 않는다.
    if (body.selected_items.length === 0 && !body.attach_to_calendar) {
      return apiError(422, "validation_failed", "저장할 내용이 없어요", {
        field: "selected_items",
      });
    }

    // 문서는 **사진에서 읽어낸 일시**로, 활동은 **부모가 열어 둔 날짜**로 간다 (lane 마다 다르다).
    const when = body.lane === "document" ? run.when : null;
    const attaching = body.attach_to_calendar;
    const attachedDate = body.lane === "document" ? when : run.date;

    const response: PhotoCommitResponse = {
      observations:
        body.selected_items.length > 0
          ? [observationFor(body.lane, body.selected_items, when)]
          : [],
      // 🚨 문서 lane 이 일시를 읽어냈을 때만 일정이 생기고, 그것도 **draft** 다.
      //    확정은 09 화면의 `POST /events/{eid}/confirm`(승인 게이트 ㉠) 뿐이다.
      event:
        body.lane === "document" && attaching && when
          ? draftEvent({
              id: `e_photo_${Date.now()}`,
              title: "물놀이",
              starts_at: `${when}T00:00:00+09:00`,
              all_day: true,
              category: "institution",
              created_by: "agent",
              source_notice_id: `n_${params.rid}`,
              source_refs: [],
              items: body.selected_items.map((name, i) => ({
                item_id: `i_photo_${i}`,
                item_name: name,
                is_prepared: false,
                prepared_at: null,
              })),
            })
          : null,
      calendar_date: attaching ? attachedDate : null,
    };

    return HttpResponse.json(response, { status: 201 });
  }),
];
