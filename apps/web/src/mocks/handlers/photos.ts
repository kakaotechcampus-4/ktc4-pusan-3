import { http, HttpResponse } from "msw";

import type {
  EventDraft,
  Observation,
  PhotoCommitRequest,
  PhotoCommitResponse,
  PhotoEntry,
  PhotoLane,
} from "@/lib/api/types";
import { PHOTO_LANES } from "@/lib/api/types";

import { CHILD_ID, daysAgo } from "../fixtures";
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
  /** 🚨 부모가 시트에서 **고른** lane. 업로드 multipart 의 `lane` 필드로 들어온다. */
  declared: PhotoLane;
  /** 서버가 읽어 보니 어느 쪽이더라. 보통 `declared` 와 같고, 시나리오로 어긋나게 만든다. */
  guess: PhotoLane;
  confidence: number;
  raw_text: string;
  /** 문서 lane 에서 읽어낸 항목들. 🚨 알림장은 여러 개, 식단표는 한 달치다. */
  entries: PhotoEntry[];
  /** 활동 lane 에서 뽑아낸 태그. */
  tags: string[];
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
 * 🚨 **무엇을 읽어낼지는 부모가 고른 lane 이 정한다.** 문서를 골랐으면 글자를, 활동을 골랐으면
 *    태그를 돌려준다 — 그래서 두 lane 을 보는 데 시나리오가 필요 없다(시트에서 고르면 된다).
 *    시나리오가 만드는 것은 **실서버로 못 만드는 상태**뿐이다: 안 읽히는 사진, 고른 것과
 *    어긋난 추측, 그리고 **한 달치 식단표**(진짜 식단표가 있어야 만들 수 있다).
 *
 * 🚨 **픽스처에 실제 알림장 문구나 아이 정보를 넣지 않는다** (저장소가 public · 최상위 §9).
 */
function readPhoto(declared: PhotoLane, date: string | null): PhotoRun {
  const scenario = currentScenario();
  const base = { declared, guess: declared, confidence: 0.86, date, unreadable: false };

  if (scenario === "photo_unreadable") {
    return { ...base, confidence: 0.3, raw_text: "", entries: [], tags: [], unreadable: true };
  }

  if (declared === "activity") {
    return {
      ...base,
      // 활동 사진에는 읽을 글자가 없다. 빈 `raw_text` 가 그 사실이다.
      raw_text: "",
      entries: [],
      tags: ["블록 쌓기", "실내 놀이", "오래 앉아 있기", "혼자 놀기"],
    };
  }

  // 식단표 한 장 = 거의 한 달치. 접힘이 필요한 이유가 이 숫자다.
  const doc =
    scenario === "photo_meal_plan"
      ? { raw_text: mealPlanText(), entries: mealPlanEntries() }
      : { raw_text: noticeText(), entries: noticeEntries() };

  if (scenario === "photo_lane_mismatch") {
    // 🚨 화면은 **고른 쪽을 그대로 두고** 한 줄로만 알린다 — 추측이 선언을 덮지 않는다.
    return { ...base, ...doc, tags: [], guess: "activity", confidence: 0.79 };
  }

  return { ...base, ...doc, tags: [] };
}

/* ── 문서 픽스처 ──────────────────────────────────────────────────────── */

function noticeText(): string {
  return [
    "9월 넷째 주 알림장",
    "월요일 도서관 나들이가 있어요. 준비물은 없어요.",
    "수요일 원복을 입고 등원해 주세요.",
    "금요일 물놀이가 있어요. 준비물: 수건, 수영복, 여벌 옷",
    "이달 말 생일잔치 날짜는 추후 공지합니다.",
  ].join("\n");
}

/**
 * 알림장 한 장에서 나온 항목들. 🚨 **일부러 못 읽은 것을 섞는다** — 화면이 두 무리로 가르는
 * 것이 이 목의 요점이고, 전부 잘 읽힌 상태만 있으면 "확인이 필요해요" 를 볼 수가 없다.
 */
function noticeEntries(): PhotoEntry[] {
  return [
    {
      id: "pe_1",
      kind: "event",
      title: "도서관 나들이",
      date: daysAgo(-5),
      all_day: true,
      items: [],
      needs_review: false,
    },
    {
      id: "pe_2",
      kind: "event",
      title: "원복 입고 등원",
      date: daysAgo(-3),
      all_day: true,
      items: ["원복"],
      needs_review: false,
    },
    {
      id: "pe_3",
      kind: "event",
      title: "물놀이",
      date: daysAgo(-1),
      all_day: true,
      items: ["수건", "수영복", "여벌 옷"],
      needs_review: false,
    },
    {
      id: "pe_4",
      kind: "event",
      title: "생일잔치",
      // 🚨 못 읽은 값은 `null` 이다. 서버가 오늘로 채우지 않는다.
      date: null,
      items: [],
      needs_review: true,
      review_reason: "날짜가 '추후 공지' 로 적혀 있어서 읽지 못했어요.",
    },
  ];
}

function mealPlanText(): string {
  return "9월 식단표\n평일 점심 식단이 날짜별로 적혀 있어요.";
}

/**
 * 한 달치 식단. 🚨 **스무 개가 넘는다** — "잘 읽은 것" 이 기본으로 접혀 있어야 하는 이유다.
 * 메뉴 이름은 지어낸 것이다 (실제 배포본을 붙여넣지 않는다).
 */
function mealPlanEntries(): PhotoEntry[] {
  const menus = [
    "쌀밥, 미역국, 두부조림",
    "보리밥, 된장국, 애호박볶음",
    "잡곡밥, 콩나물국, 달걀말이",
    "쌀밥, 무국, 채소볶음",
    "흑미밥, 배춧국, 감자조림",
  ];

  const entries: PhotoEntry[] = Array.from({ length: 21 }, (_, i) => ({
    id: `pe_meal_${i}`,
    kind: "meal" as const,
    title: menus[i % menus.length],
    date: daysAgo(-(i + 1)),
    all_day: true,
    items: [],
    needs_review: false,
  }));

  // 한 칸은 접혀서 안 보이는 자리에도 못 읽은 것이 있을 수 있다는 것을 보여준다.
  entries[7] = {
    ...entries[7],
    title: "읽지 못한 칸",
    date: null,
    needs_review: true,
    review_reason: "글자가 겹쳐 있어서 이 칸은 읽지 못했어요.",
  };

  return entries;
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

  yield frame("lane", { guess: run.guess, confidence: run.confidence });
  await sleep(300);

  yield frame("step", { index: 2, total: 2, label: "읽은 것을 정리하고 있어요" });
  await sleep(500);

  yield frame("parsed", {
    raw_text: run.raw_text,
    // 🚨 문서와 활동은 **다른 필드**다. 한 배열에 섞지 않는다 (types.ts 의 🚨).
    entries: run.entries,
    tags: run.tags,
  });
  await sleep(200);

  yield frame("done", { run_id: runId, model_calls: 1 });
}

/** 저장할 때 만드는 관찰 1건. `lane` 이 `kind` 와 `confidence_source` 를 함께 정한다. */
function documentObservation(entries: PhotoEntry[]): Observation {
  const observedOn = entries.find((e) => e.date !== null)?.date ?? daysAgo(0);
  const items = entries.flatMap((e) => e.items);

  return {
    id: `o_photo_${Date.now()}`,
    child_id: CHILD_ID,
    kind: "observation_education",
    raw_text: `알림장에서 읽었어요. ${entries.map((e) => e.title).join(", ")}`,
    subject: entries[0]?.title ?? "알림장",
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
    domain_fields: { items, entries: entries.length },
  };
}

function activityObservation(tags: string[], when: string | null): Observation {
  const observedOn = when ?? daysAgo(0);

  return {
    id: `o_photo_${Date.now()}`,
    child_id: CHILD_ID,
    kind: "observation_activity",
    raw_text: `사진에서 고른 활동: ${tags.join(", ")}`,
    subject: tags[0] ?? "활동",
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
    domain_fields: { tags },
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

      // 🚨 `lane` 은 필수다. 빠지면 서버가 추측해서 저장 경로를 정하게 되는데,
      //    문서와 활동은 `confidence_source` 가 통째로 다르다 (계약서 §09).
      const formLane = form.get("lane");
      if (typeof formLane !== "string" || !PHOTO_LANES.includes(formLane as PhotoLane)) {
        return apiError(422, "validation_failed", "어떤 사진인지 알려주세요", { field: "lane" });
      }

      const formDate = form.get("date");
      const runId = `pr_${Date.now()}`;
      photoRuns.set(
        runId,
        readPhoto(formLane as PhotoLane, typeof formDate === "string" ? formDate : null),
      );
      return HttpResponse.json({ run_id: runId }, { status: 202 });
    }),
  ),

  /*
   * ⚠️ `POST /photo-runs/{rid}/reanalyze` 핸들러는 **없다.** 화면에서 뺐기 때문이다 —
   *    읽어낸 항목을 부모가 그 자리에서 고칠 수 있게 되면서 "모델에게 다시 맡기기" 가
   *    더 먼 길이 됐다. 엔드포인트는 계약서에 남아 있고, 필요해지면 여기 다시 만든다.
   */

  /** 🚨 사진 흐름에서 **저장이 일어나는 유일한 지점**이다. */
  http.post(url("/photo-runs/:rid/commit"), async ({ request, params }) => {
    await networkDelay();

    const run = photoRuns.get(String(params.rid));
    if (!run) return apiError(404, "not_found", "저장할 사진이 없어요");

    const body = (await request.json()) as PhotoCommitRequest;
    const entries = body.entries ?? [];
    const tags = body.selected_tags ?? [];

    // 🚨 **확인하지 않은 항목이 오면 버그다.** 화면이 빼고 보내야 하는데 서버까지 왔다는 뜻이라,
    //    조용히 저장하지 않고 막는다 — "승인 전에는 저장되지 않아요" 가 여기서 지켜진다.
    const unchecked = entries.filter((e) => e.needs_review);
    if (unchecked.length > 0) {
      return apiError(422, "validation_failed", "확인하지 않은 항목이 있어요", {
        field: "entries",
        ids: unchecked.map((e) => e.id),
      });
    }

    // 저장할 내용이 없으면 빈 저장을 만들지 않는다.
    if (entries.length === 0 && tags.length === 0) {
      return apiError(422, "validation_failed", "저장할 내용이 없어요", {
        field: body.lane === "document" ? "entries" : "selected_tags",
      });
    }

    // 문서는 **부모가 확인한 항목의 일시**로, 활동은 **부모가 열어 둔 날짜**로 간다.
    const when =
      body.lane === "document" ? (entries.find((e) => e.date !== null)?.date ?? null) : null;
    const attaching = body.attach_to_calendar;
    const attachedDate = body.lane === "document" ? when : run.date;

    /**
     * 🚨 **날짜를 읽어낸 `event` 항목마다 초안 한 장이다.** 알림장 한 장에 일정이 여러 개 적혀
     *    있는데, 계약서 §09 의 `event`(단수)로는 첫 항목만 일정이 되고 나머지는 사라졌다.
     * 🚨 **`kind` 를 반드시 본다.** 날짜만 보고 거르면 **한 달치 식단표가 초안 21장**이 된다 —
     *    급식(`meal`)은 전부 날짜가 있고, 그건 일정이 아니라 그날의 식사 기록이다.
     * 🚨 **아무것도 저장하지 않는다.** 저장은 보호자가 카드에서 제출할 때 한 번이다 (게이트 ㉠).
     */
    const drafts: EventDraft[] =
      body.lane === "document" && attaching
        ? entries
            .filter((entry) => entry.kind === "event" && entry.date !== null)
            .map((entry) => ({
              draft_id: `d_photo_${entry.id}`,
              op: "create" as const,
              event_id: null,
              event: {
                title: entry.title,
                starts_at: `${entry.date}T00:00:00+09:00`,
                ends_at: null,
                all_day: entry.all_day ?? true,
                event_type: "episodic" as const,
                category: "institution" as const,
              },
              before: null,
              items: entry.items.map((name) => ({ item_id: null, item_name: name })),
            }))
        : [];

    const response: PhotoCommitResponse = {
      observations:
        body.lane === "document"
          ? entries.length > 0
            ? [documentObservation(entries)]
            : []
          : tags.length > 0
            ? [activityObservation(tags, attachedDate)]
            : [],
      drafts,
      calendar_date: attaching ? attachedDate : null,
    };

    return HttpResponse.json(response, { status: 201 });
  }),
];
