import { http, HttpResponse } from "msw";

import type {
  AffinitiesResponse,
  Affinity,
  CorrectionRequest,
  CorrectionResponse,
  Observation,
  ObservationDetailResponse,
  ObservationsResponse,
  SuggestionFeedback,
  SuggestionFeedbackResponse,
  SuggestionListResponse,
} from "@/lib/api/types";

import {
  affinities,
  allObservations,
  healthSafety,
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

/** 교정으로 비활성이 된 관찰. `wrong` · `outdated` 는 행을 지우지 않고 `inactive` 로 내린다. */
const inactivatedObservations = new Set<string>();

export function resetMemoryState(): void {
  feedbackBySuggestion.clear();
  inactivatedObservations.clear();
}

function scenarioAffinities(): Affinity[] {
  const scenario = currentScenario();
  if (scenario === "empty") return [];
  if (scenario === "stale") return staleAffinities;
  return affinities;
}

/**
 * 목록의 기본은 **살아 있는 기억**이다. 교정으로 내려간 것은 빠진다 (계약서 §08).
 *
 * ⚠️ `status=inactive` 는 계약서 v1 에 아직 없는 파라미터다. 교정으로 뺀 기억을 되돌릴 경로가
 *    화면에 있어야 해서(`memories/page.tsx` 의 🚨) 제안 형태로 목에 먼저 세웠다.
 *    👉 `apps/api` Owner 협의 대상 (최상위 CLAUDE.md §8).
 */
function scenarioObservations(inactiveOnly = false): Observation[] {
  if (currentScenario() === "empty") return [];
  return (
    allObservations
      .filter((o) => inactivatedObservations.has(`${o.kind}:${o.id}`) === inactiveOnly)
      // 목록이 내려주는 status 도 실제 상태와 맞춰 둔다 — 화면이 이 값으로 갈리는 날 어긋나지 않게.
      .map((o) => (inactiveOnly ? { ...o, status: "inactive" as const } : o))
  );
}

export const memoryHandlers = [
  http.get(url("/children/:cid/observations"), async ({ request }) => {
    await networkDelay();

    const params = new URL(request.url).searchParams;
    const domain = params.get("domain");
    const unusedOnly = params.get("unused_in_suggestions") === "true";
    const inactiveOnly = params.get("status") === "inactive";

    let items = scenarioObservations(inactiveOnly);
    if (domain) items = items.filter((o) => o.kind === `observation_${domain}`);
    // "제안에서 빠진 기억" — 목에서는 프로필에 묶이지 않은 것을 그 자리에 둔다.
    if (unusedOnly) {
      items = items.filter((o) => o.kind === "observation_health" || o.affinity === null);
    }

    const body: ObservationsResponse = { items, next_cursor: null, total: items.length };
    return HttpResponse.json(body);
  }),

  http.get(url("/children/:cid/observations/:kind/:id"), async ({ params }) => {
    await networkDelay();

    const observation = allObservations.find((o) => o.kind === params.kind && o.id === params.id);
    if (!observation) return apiError(404, "not_found", "그 기억을 찾지 못했어요");

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
      corrections: [],
    };
    return HttpResponse.json(body);
  }),

  http.get(url("/children/:cid/affinities"), async () => {
    await networkDelay();
    const body: AffinitiesResponse = {
      affinities: scenarioAffinities(),
      // 🚨 안전 정보는 감쇠가 없다 — 기억이 비어도 여기는 비지 않는다.
      safety: healthSafety,
    };
    return HttpResponse.json(body);
  }),

  http.post(url("/corrections"), async ({ request }) => {
    await networkDelay();
    const body = (await request.json()) as CorrectionRequest;

    if (Array.isArray(body.target_ref)) {
      return apiError(422, "validation_failed", "target_ref 는 객체 1개예요");
    }

    if (body.target_ref.kind === "profile_affinity") {
      const target = scenarioAffinities().find((a) => a.id === body.target_ref.id);
      if (!target) return apiError(404, "not_found", "그 프로필을 찾지 못했어요");

      const response: CorrectionResponse = {
        correction: {
          id: `cr_${Date.now()}`,
          verdict: body.verdict,
          created_at: new Date().toISOString(),
        },
        target: nextAffinity(target, body.verdict),
        cascade: {
          affinities_recomputed: [{ kind: "profile_affinity", id: target.id }],
          suggestions_recalculated: body.verdict === "confirm" ? [] : ["s_1"],
        },
      };
      return HttpResponse.json(response, { status: 201 });
    }

    const target = allObservations.find(
      (o) => o.kind === body.target_ref.kind && o.id === body.target_ref.id,
    );
    if (!target) return apiError(404, "not_found", "그 기억을 찾지 못했어요");

    // 🚨 하드 삭제가 아니다 — `status` 를 내린다. 행이 남아야 제안의 source_refs 가 안 끊긴다.
    if (body.verdict === "outdated" || body.verdict === "wrong") {
      inactivatedObservations.add(`${target.kind}:${target.id}`);
    }

    const response: CorrectionResponse = {
      correction: {
        id: `cr_${Date.now()}`,
        verdict: body.verdict,
        created_at: new Date().toISOString(),
      },
      target: {
        ...target,
        status: body.verdict === "confirm" || body.verdict === "once_only" ? "active" : "inactive",
        confidence_source: body.verdict === "confirm" ? "parent_direct" : target.confidence_source,
      },
      cascade: {
        affinities_recomputed:
          target.kind === "observation_health"
            ? []
            : target.affinity
              ? [{ kind: "profile_affinity", id: target.affinity.id }]
              : [],
        suggestions_recalculated: body.verdict === "confirm" ? [] : ["s_1"],
      },
    };
    return HttpResponse.json(response, { status: 201 });
  }),

  /**
   * ⚠️ **계약서 v1 에 없는 엔드포인트다** (types.ts 의 ⚠️). 07 피드백 탭이 평가할 제안을
   *    목록으로 얻을 길이 없어서 제안해 두고 목으로 먼저 세웠다 — `apps/api` Owner 협의 대상.
   */
  http.get(url("/children/:cid/suggestions"), async () => {
    await networkDelay();
    const items =
      currentScenario() === "empty"
        ? []
        : receivedSuggestions.map((suggestion) => ({
            ...suggestion,
            feedback: feedbackBySuggestion.get(suggestion.id) ?? suggestion.feedback,
          }));
    const body: SuggestionListResponse = { items, next_cursor: null };
    return HttpResponse.json(body);
  }),

  http.patch(url("/suggestions/:sid/feedback"), async ({ params, request }) => {
    await networkDelay();
    const { feedback } = (await request.json()) as { feedback: SuggestionFeedback };
    const sid = String(params.sid);

    const suggestion = receivedSuggestions.find((s) => s.id === sid);
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

/**
 * 기억 교정의 효과 표 (계약서 §08). 프론트가 추측하지 않게 목도 그대로 따른다.
 *
 * ⚠️ `need_more_observation` 은 계약서 v1 에 없는 값이다 (types.ts 의 ⚠️). "아직 확정하지 말고
 *    더 지켜보자" 라서 **한 단계만** 내린다 — v1 에서 `once_only` 가 하던 자리와 같다.
 */
function nextAffinity(target: Affinity, verdict: CorrectionRequest["verdict"]): Affinity {
  if (verdict === "confirm") {
    return { ...target, strength: Math.min(1, target.strength + 0.1) };
  }
  if (verdict === "need_more_observation" || verdict === "once_only") {
    return target.state === "confirmed" ? { ...target, state: "candidate" } : target;
  }
  return { ...target, state: "archived" };
}
