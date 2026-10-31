import { http, HttpResponse } from "msw";

import {
  type AffinitiesResponse,
  type Affinity,
  type Agent,
  type CorrectionRequest,
  type CorrectionResponse,
  type Observation,
  type ObservationKind,
  type ObservationDetailResponse,
  type ObservationsResponse,
  type SuggestionFeedback,
  type SuggestionFeedbackResponse,
  type SuggestionListResponse,
} from "@/lib/api/types";

import {
  affinities,
  allObservations,
  healthSafety,
  manyObservations,
  manyReceivedSuggestions,
  receivedSuggestions,
  staleAffinities,
} from "../fixtures";
import { currentScenario } from "../scenario";
import { apiError, networkDelay, url } from "./helpers";

/**
 * 07 기억 · 교정 핸들러.
 *
 * 🚨 **관찰과 프로필은 다른 엔드포인트다** (계약서 §08). 목에서도 갈라 둬야 화면이
 *    둘을 섞지 않았는지 확인할 수 있다.
 */

/** 07 피드백 탭이 보낸 평가. 🚨 목은 프로세스 수명만큼 사는 상태를 들고 있다. */
const feedbackBySuggestion = new Map<string, SuggestionFeedback>();

/**
 * 교정으로 상태가 바뀐 관찰. 행을 지우지 않고 상태만 내린다 — 서버(#277)와 같은 표다.
 * `once_only` → `stand_alone` · `wrong` → `inactive`.
 *
 * 🚨 **고친 기록도 목록에 남는다** (#266). 서버는 `deleted` 만 빼고 세 상태를 모두 내리고,
 *    화면이 `status` 로 갈라 그린다. 고치기는 `active` 기록만 받는다 — 아니면 409 `already_corrected`.
 */
const correctedObservations = new Map<string, "stand_alone" | "inactive">();
/** 기록 상세의 `corrections` — 서버처럼 고친 이력을 상세가 돌려준다. */
const correctionHistory = new Map<string, ObservationDetailResponse["corrections"]>();

/**
 * 기억 고치기가 남긴 것. 서버(#277)처럼 **상태를 직접 쓰지 않는다** — `wrong` 수와 `strength` 만
 * 들고, 상태는 `currentAffinity` 가 기록 수로 다시 센다.
 */
const wrongsByAffinity = new Map<string, number>();
const strengthByAffinity = new Map<string, number>();

/**
 * 서버 `rules/profile.py` 의 두 값. 🚨 화면은 이 값을 모른다 — 목이 서버와 같은 결과를 내려고만 둔다.
 * `PROMOTION_THRESHOLD`: 확인됨이 되려면 묶인 기록이 이만큼 + 21일 안의 기억 `wrong` 수.
 */
const PROMOTION_THRESHOLD = 3;
const VERDICT_DECAY: Record<"need_more_observation" | "outdated" | "wrong", number> = {
  wrong: 0.93,
  outdated: 0.95,
  need_more_observation: 0.97,
};

/**
 * 화면의 분류(Agent) → 읽을 관찰 테이블. 서버(#266 `_DOMAINS_BY_AGENT`)와 같은 표다.
 *
 * 🚨 **`observation_${domain}` 으로 유추하지 않는다.** `growth` 는 education 과 routine 두 테이블이고,
 *    그렇게 찾으면 없는 테이블(`observation_growth`)을 찾아 "성장" 이 늘 비었다 (#269).
 * 🚨 **건강은 첫 배포 범위 밖이다** (#259). 서버는 목록에서 빼고 `domain=health` 에 빈 목록을 준다.
 */
const KINDS_BY_AGENT: Record<Agent, readonly ObservationKind[]> = {
  food: ["observation_food"],
  activity: ["observation_activity"],
  growth: ["observation_education", "observation_routine"],
  health: [],
};
const VISIBLE_KINDS: readonly ObservationKind[] = Object.values(KINDS_BY_AGENT).flat();

/** 서버의 기본 쪽 크기. 화면은 `size` 를 보내지 않는다. */
const PAGE_SIZE = 20;
/** 서버(`api/v1/list_query.py`)의 `page` 상한. 넘으면 400 이다. */
const MAX_PAGE = 10_000;
const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;

/**
 * 쪽만 받는 쿼리 — 서버 `list_query.page_query` 와 같은 검증이다. 제안 목록(#592)은 이것만 받는다.
 * 🚨 서버는 쿼리 검증 실패를 422 가 아니라 **400** 으로 돌려준다 (422 는 경로 값이 틀렸을 때다).
 */
function readPageQuery(params: URLSearchParams): { page: number; size: number } | Response {
  const page = Number(params.get("page") ?? 1);
  const size = Number(params.get("size") ?? PAGE_SIZE);

  if (!Number.isInteger(page) || page < 1 || page > MAX_PAGE) {
    return apiError(400, "validation_failed", "쪽 번호가 올바르지 않아요");
  }
  if (!Number.isInteger(size) || size < 1 || size > 100) {
    return apiError(400, "validation_failed", "size 는 1~100 이에요");
  }
  return { page, size };
}

/** 기록 · 기억 목록이 같이 받는 쿼리 (#584) — 서버 `list_query` 와 같은 검증이다. 쪽은 위와 같다. */
function readListQuery(
  params: URLSearchParams,
):
  | { page: number; size: number; from: string | null; to: string | null; q: string | null }
  | Response {
  const paging = readPageQuery(params);
  if (paging instanceof Response) return paging;
  const { page, size } = paging;
  const from = params.get("date_from");
  const to = params.get("date_to");
  const q = params.get("q");

  if ((from !== null && !ISO_DATE.test(from)) || (to !== null && !ISO_DATE.test(to))) {
    return apiError(400, "validation_failed", "날짜 형식이 올바르지 않아요");
  }
  if (from !== null && to !== null && from > to) {
    return apiError(400, "validation_failed", "시작 날짜가 끝 날짜보다 늦을 수 없어요");
  }
  if (q !== null && q.length > 100) {
    return apiError(400, "validation_failed", "검색어는 100자까지예요");
  }
  return { page, size, from, to, q: q?.trim() || null };
}

/** 한 쪽을 잘라 네 칸과 함께. 범위를 넘은 쪽은 오류가 아니라 빈 목록이다 (서버와 같다). */
function slicePage<T>(rows: T[], page: number, size: number) {
  return {
    items: rows.slice((page - 1) * size, page * size),
    total: rows.length,
    page,
    size,
    total_pages: Math.ceil(rows.length / size),
  };
}

export function resetMemoryState(): void {
  feedbackBySuggestion.clear();
  correctedObservations.clear();
  correctionHistory.clear();
  wrongsByAffinity.clear();
  strengthByAffinity.clear();
}

function scenarioAffinities(): Affinity[] {
  const scenario = currentScenario();
  if (scenario === "empty") return [];
  return (scenario === "stale" ? staleAffinities : affinities).map(currentAffinity);
}

/**
 * 고치기를 반영한 기억. 서버(#277 `recompute_profile`)와 같은 규칙이다.
 *
 * - 묶인 기록은 `active` 만 센다 — 고친 기록(`stand_alone` · `inactive`)은 빠진다
 * - 상태는 기록 수와 기억 `wrong` 수로만 정한다. `need_more_observation` · `outdated` 는
 *   `strength` 만 낮추고 상태를 바꾸지 않는다. 🚨 **어느 판정도 기억을 `archived` 로 보내지 않는다**
 *
 * ⚠️ 서버는 최근 14일 기록 수와 강한 신호(G)도 보지만 목은 묶인 기록 수만 센다. 그래서 고치기가
 *    닿지 않은 기억은 픽스처 그대로 둔다 — 다시 세면 픽스처의 상태가 목 규칙으로 바뀐다.
 */
function currentAffinity(affinity: Affinity): Affinity {
  const activeRefs = affinity.source_refs.filter(
    (ref) => !correctedObservations.has(`${ref.kind}:${ref.id}`),
  );
  const wrongs = wrongsByAffinity.get(affinity.id) ?? 0;
  const strength = strengthByAffinity.get(affinity.id);
  if (activeRefs.length === affinity.source_refs.length && wrongs === 0 && strength === undefined) {
    return affinity;
  }

  const count = activeRefs.length;
  const state =
    affinity.state === "archived"
      ? "archived"
      : count >= PROMOTION_THRESHOLD + wrongs
        ? "confirmed"
        : "candidate";
  return {
    ...affinity,
    state,
    strength: strength ?? affinity.strength,
    observation_count: count,
    source_refs: activeRefs,
  };
}

/**
 * 기록 목록. 서버(#266)처럼 고친 기록(`stand_alone` · `inactive`)도 **빼지 않고** `status` 만
 * 고친 값으로 내린다 — 화면이 그 값으로 줄을 갈라 그린다. `total` 도 세 상태를 모두 센다.
 */
function scenarioObservations(): Observation[] {
  const scenario = currentScenario();
  if (scenario === "empty") return [];
  return (scenario === "observations_many" ? manyObservations : allObservations).map(
    withCorrectedStatus,
  );
}

/**
 * 기록 탭 목록과 같은 기준의 기록 전부 — 같은 표 · 같은 상태. 09 캘린더가 이것을 센다 (#318).
 *
 * 🚨 서버도 캘린더가 기록 탭의 상수(`LISTED_STATUSES` · `VISIBLE_DOMAINS`)를 그대로 가져다 쓴다.
 *    목이 두 곳에서 따로 거르면 달 숫자를 눌렀을 때 기록 탭과 다른 목록이 나온다.
 */
export function listedObservations(): Observation[] {
  return scenarioObservations().filter((o) => VISIBLE_KINDS.includes(o.kind));
}

function withCorrectedStatus<T extends Observation>(observation: T): T {
  const status = correctedObservations.get(`${observation.kind}:${observation.id}`);
  return status ? { ...observation, status } : observation;
}

export const memoryHandlers = [
  http.get(url("/children/:cid/observations"), async ({ request }) => {
    await networkDelay();

    const params = new URL(request.url).searchParams;
    const domain = params.get("domain");
    const status = params.get("status");
    const query = readListQuery(params);
    if (query instanceof Response) return query;

    const kinds = domain === null ? VISIBLE_KINDS : KINDS_BY_AGENT[domain as Agent];
    if (!kinds) return apiError(400, "validation_failed", "모르는 분류예요");

    // 서버처럼 `status` 는 하나만 고른다 — 주지 않으면 세 상태 모두다 (#266).
    if (status !== null && !["active", "stand_alone", "inactive"].includes(status)) {
      return apiError(400, "validation_failed", "모르는 상태예요");
    }
    const needle = query.q?.toLowerCase();
    const rows = scenarioObservations().filter(
      (o) =>
        kinds.includes(o.kind) &&
        (status === null || o.status === status) &&
        // 서버처럼 관찰 기간이 범위와 **겹치면** 든다 — 양 끝 날짜 포함.
        (query.from === null || o.observed_to >= query.from) &&
        (query.to === null || o.observed_from <= query.to) &&
        // 원문과 주제의 부분 일치. 기록은 띄어쓰기를 그대로 비교한다 (기억과 다르다).
        (needle === undefined ||
          o.raw_text.toLowerCase().includes(needle) ||
          ("subject" in o && o.subject.toLowerCase().includes(needle))),
    );

    const body: ObservationsResponse = slicePage(rows, query.page, query.size);
    return HttpResponse.json(body);
  }),

  http.get(url("/children/:cid/observations/:kind/:id"), async ({ params }) => {
    await networkDelay();

    const found = allObservations.find((o) => o.kind === params.kind && o.id === params.id);
    if (!found) return apiError(404, "not_found", "그 기록을 찾지 못했어요");
    const observation = withCorrectedStatus(found);

    /** 🚨 `used_in` 이 빈 배열이면 화면은 "제안 근거에서 빠져 있어요" 를 그린다. */
    const usedIn =
      observation.kind === "observation_food"
        ? [
            {
              suggestion_id: "s_1",
              content: "계란말이에 시금치를 조금 섞어 보세요",
              status: "draft" as const,
            },
          ]
        : [];

    const body: ObservationDetailResponse = {
      observation,
      used_in: usedIn,
      corrections: correctionHistory.get(`${found.kind}:${found.id}`) ?? [],
    };
    return HttpResponse.json(body);
  }),

  http.get(url("/children/:cid/affinities"), async ({ request }) => {
    await networkDelay();

    // 계약서 §08 이 주는 필터 둘. 🚨 정렬 파라미터는 없다 — 화면도 정렬을 만들지 않는다.
    const params = new URL(request.url).searchParams;
    const domain = params.get("domain");
    const state = params.get("state");
    const query = readListQuery(params);
    if (query instanceof Response) return query;

    // 서버 기본값: `archived` 와 묶인 active 기록이 없는 기억은 빠진다 (#268).
    // 🚨 쪽으로 자르기 **전에** 거른다 — 자른 뒤에 거르면 쪽마다 개수가 다르고 total 이 부푼다 (#584).
    let affinities = scenarioAffinities().filter((a) => a.observation_count > 0);
    if (domain) affinities = affinities.filter((a) => a.domain === domain);
    affinities = state
      ? affinities.filter((a) => a.state === state)
      : affinities.filter((a) => a.state !== "archived");
    // 날짜는 마지막 기록 날짜 기준이다 (기록의 "기간이 겹치면" 과 다르다).
    const { from, to } = query;
    if (from !== null) affinities = affinities.filter((a) => a.last_observed_on >= from);
    if (to !== null) affinities = affinities.filter((a) => a.last_observed_on <= to);
    // 이름 검색은 띄어쓰기를 무시한다 — "계란반찬" 으로 "계란 반찬" 이 찾아진다 (서버와 같다).
    const needle = query.q?.replace(/\s/g, "").toLowerCase();
    if (needle) {
      affinities = affinities.filter((a) =>
        a.merge_key.replace(/\s/g, "").toLowerCase().includes(needle),
      );
    }

    const { items, ...page } = slicePage(affinities, query.page, query.size);
    const body: AffinitiesResponse = {
      affinities: items,
      ...page,
      // 🚨 안전 정보는 감쇠가 없다 — 기억이 비어도 여기는 비지 않는다. 쪽으로 나누지도 않는다.
      safety: healthSafety,
    };
    return HttpResponse.json(body);
  }),

  http.post(url("/corrections"), async ({ request }) => {
    await networkDelay();
    const body = (await request.json()) as CorrectionRequest;

    // 서버는 본문 검증 실패를 400 으로 돌려준다 (422 는 경로 값이 틀렸을 때다)
    if (Array.isArray(body.target_ref)) {
      return apiError(400, "validation_failed", "target_ref 는 객체 1개예요");
    }

    if (body.target_ref.kind === "profile_affinity") {
      // 서버처럼 목록에 보이는 기억만 고친다 — 묶인 active 기록이 없으면 404.
      const found = scenarioAffinities().find((a) => a.id === body.target_ref.id);
      if (!found || found.observation_count === 0) {
        return apiError(404, "not_found", "그 기억을 찾지 못했어요");
      }
      if (body.verdict === "once_only") {
        return apiError(400, "validation_failed", "이 대상에는 쓸 수 없는 고치기예요");
      }

      strengthByAffinity.set(found.id, found.strength * VERDICT_DECAY[body.verdict]);
      if (body.verdict === "wrong") {
        wrongsByAffinity.set(found.id, (wrongsByAffinity.get(found.id) ?? 0) + 1);
      }
      const target = scenarioAffinities().find((a) => a.id === found.id) ?? found;

      const response: CorrectionResponse = {
        correction: {
          id: `cr_${Date.now()}`,
          verdict: body.verdict,
          created_at: new Date().toISOString(),
        },
        target,
        cascade: {
          affinities_recomputed: [{ kind: "profile_affinity", id: target.id }],
          suggestions_recalculated: ["s_1"],
        },
      };
      return HttpResponse.json(response, { status: 201 });
    }

    const target = allObservations.find(
      (o) => o.kind === body.target_ref.kind && o.id === body.target_ref.id,
    );
    if (!target) return apiError(404, "not_found", "그 기록을 찾지 못했어요");
    if (body.verdict !== "once_only" && body.verdict !== "wrong") {
      return apiError(400, "validation_failed", "이 대상에는 쓸 수 없는 고치기예요");
    }
    // 🚨 고치기는 `active` 기록만 받는다 (#277). 값이 틀린 400 과 코드가 다르다 — 화면은 이 409 를
    //    "이미 고친 기록" 으로 읽고 버튼을 거둔다.
    if (correctedObservations.has(`${target.kind}:${target.id}`)) {
      return apiError(409, "already_corrected", "이미 고친 기록이에요");
    }

    // 🚨 하드 삭제가 아니다 — `status` 를 내린다. 행이 남아야 제안의 source_refs 가 안 끊긴다.
    const status = body.verdict === "once_only" ? "stand_alone" : "inactive";
    correctedObservations.set(`${target.kind}:${target.id}`, status);
    const correction = {
      id: `cr_${Date.now()}`,
      verdict: body.verdict,
      created_at: new Date().toISOString(),
    };
    correctionHistory.set(`${target.kind}:${target.id}`, [correction]);

    const response: CorrectionResponse = {
      correction,
      target: { ...target, status },
      cascade: {
        affinities_recomputed:
          target.kind === "observation_health"
            ? []
            : target.affinity
              ? [{ kind: "profile_affinity", id: target.affinity.id }]
              : [],
        suggestions_recalculated: ["s_1"],
      },
    };
    return HttpResponse.json(response, { status: 201 });
  }),

  /**
   * 07 피드백 탭이 평가할 제안 목록 (#284). 개인화와 채택한 일반 추천(#509)이 같이 온다.
   * 🚨 **`rated` 를 건 뒤에 쪽을 자른다** (#592) — 서버와 같다. 평가하면 피드백 전에서 빠지고
   *    뒤의 줄이 한 칸씩 당겨진다. `true` · `false` 가 아닌 값은 400 이다.
   */
  http.get(url("/children/:cid/suggestions"), async ({ request }) => {
    await networkDelay();
    const params = new URL(request.url).searchParams;
    const paging = readPageQuery(params);
    if (paging instanceof Response) return paging;
    const ratedParam = params.get("rated");
    if (ratedParam !== null && ratedParam !== "true" && ratedParam !== "false") {
      return apiError(400, "validation_failed", "rated 는 true 나 false 예요");
    }

    const scenario = currentScenario();
    const rows = (
      scenario === "empty"
        ? []
        : scenario === "suggestions_many"
          ? manyReceivedSuggestions
          : receivedSuggestions
    )
      .map((suggestion) => ({
        ...suggestion,
        feedback: feedbackBySuggestion.get(suggestion.id) ?? suggestion.feedback,
      }))
      .filter((suggestion) =>
        ratedParam === null ? true : (suggestion.feedback !== null) === (ratedParam === "true"),
      );
    const body: SuggestionListResponse = slicePage(rows, paging.page, paging.size);
    return HttpResponse.json(body);
  }),

  http.patch(url("/children/:cid/suggestions/:sid/feedback"), async ({ params, request }) => {
    await networkDelay();
    const { feedback } = (await request.json()) as { feedback: SuggestionFeedback };
    const sid = String(params.sid);

    const suggestion = manyReceivedSuggestions.find((s) => s.id === sid);
    if (!suggestion) return apiError(404, "not_found", "그 제안을 찾지 못했어요");

    feedbackBySuggestion.set(sid, feedback);

    /** 🚨 `memory_changed` 는 **항상 false** 다 (계약서 §08). 화면 문구가 이 값과 같은 말을 한다. */
    const body: SuggestionFeedbackResponse = {
      suggestion: { ...suggestion, feedback },
      memory_changed: false,
    };
    return HttpResponse.json(body);
  }),
];
