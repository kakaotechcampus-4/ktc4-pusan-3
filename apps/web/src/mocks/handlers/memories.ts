import { http, HttpResponse } from "msw";

import {
  isHealthObservation,
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
 * ⚠️ `stand_alone` 은 목록에 남긴다 — "이번만 그랬어요" 의 안내 문구("기록은 목록에 그대로 남아요")를
 *    따랐다. 서버 목록은 `active` 만 내려서 빠진다. 어느 쪽이 맞는지 #277 에 물어 둔 상태다.
 */
const correctedObservations = new Map<string, "stand_alone" | "inactive">();

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

/** 서버의 기본 장 크기. 화면은 `limit` 을 보내지 않는다. */
const OBSERVATION_PAGE_SIZE = 20;

/**
 * 🚨 **화면에게 커서는 불투명한 문자열이다.** 서버는 `(observed_to, kind, id)` 를 base64 로 싸고,
 *    목은 위치(offset)를 싼다. 화면이 받은 값을 그대로 돌려주기만 하면 둘 다 맞게 돈다.
 */
function encodeCursor(offset: number): string {
  return btoa(JSON.stringify({ offset }));
}

function decodeCursor(cursor: string): number | null {
  try {
    const { offset } = JSON.parse(atob(cursor)) as { offset?: unknown };
    return typeof offset === "number" && Number.isInteger(offset) && offset >= 0 ? offset : null;
  } catch {
    return null;
  }
}

export function resetMemoryState(): void {
  feedbackBySuggestion.clear();
  correctedObservations.clear();
}

function scenarioAffinities(): Affinity[] {
  const scenario = currentScenario();
  if (scenario === "empty") return [];
  if (scenario === "stale") return staleAffinities;
  return affinities;
}

/**
 * 목록의 기본은 **살아 있는 기록**이다. 교정으로 내려간 것은 빠진다 (계약서 §08).
 *
 * `status=inactive` 는 계약서에 있는 파라미터라 목도 받아 둔다. 다만 **화면은 쓰지 않는다** —
 * 교정을 되돌리는 기능을 주지 않기로 해서, 뺀 것을 다시 꺼내 보는 길도 두지 않는다.
 */
function scenarioObservations(inactiveOnly = false): Observation[] {
  const scenario = currentScenario();
  if (scenario === "empty") return [];
  return (
    (scenario === "observations_many" ? manyObservations : allObservations)
      .filter(
        (o) => (correctedObservations.get(`${o.kind}:${o.id}`) === "inactive") === inactiveOnly,
      )
      // 목록이 내려주는 status 도 실제 상태와 맞춰 둔다 — 화면이 이 값으로 갈리는 날 어긋나지 않게.
      .map((o) => ({ ...o, status: correctedObservations.get(`${o.kind}:${o.id}`) ?? o.status }))
  );
}

export const memoryHandlers = [
  http.get(url("/children/:cid/observations"), async ({ request }) => {
    await networkDelay();

    const params = new URL(request.url).searchParams;
    const domain = params.get("domain");
    const unusedOnly = params.get("unused_in_suggestions") === "true";
    const inactiveOnly = params.get("status") === "inactive";
    const limit = Number(params.get("limit") ?? OBSERVATION_PAGE_SIZE);
    const cursor = params.get("cursor");

    if (!Number.isInteger(limit) || limit < 1 || limit > 100) {
      return apiError(400, "validation_failed", "limit 은 1~100 이에요");
    }
    const offset = cursor === null ? 0 : decodeCursor(cursor);
    if (offset === null) return apiError(400, "validation_failed", "목록 위치가 올바르지 않아요");

    const kinds = domain === null ? VISIBLE_KINDS : KINDS_BY_AGENT[domain as Agent];
    if (!kinds) return apiError(400, "validation_failed", "모르는 분류예요");

    let items = scenarioObservations(inactiveOnly).filter((o) => kinds.includes(o.kind));
    // "제안에서 빠진 기억" — 목에서는 프로필에 묶이지 않은 것을 그 자리에 둔다.
    // 건강은 위에서 이미 빠졌다 — `affinity` 키가 없는 모양이라 여기 닿지 않는다.
    if (unusedOnly) items = items.filter((o) => !isHealthObservation(o) && o.affinity === null);

    const page = items.slice(offset, offset + limit);
    const next = offset + limit < items.length ? encodeCursor(offset + limit) : null;
    const body: ObservationsResponse = { items: page, next_cursor: next, total: items.length };
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

  http.get(url("/children/:cid/affinities"), async ({ request }) => {
    await networkDelay();

    // 계약서 §08 이 주는 필터 둘. 🚨 정렬 파라미터는 없다 — 화면도 정렬을 만들지 않는다.
    const params = new URL(request.url).searchParams;
    const domain = params.get("domain");
    const state = params.get("state");

    let affinities = scenarioAffinities();
    if (domain) affinities = affinities.filter((a) => a.domain === domain);
    if (state) affinities = affinities.filter((a) => a.state === state);

    const body: AffinitiesResponse = {
      affinities,
      // 🚨 안전 정보는 감쇠가 없다 — 기억이 비어도 여기는 비지 않는다.
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
          suggestions_recalculated: ["s_1"],
        },
      };
      return HttpResponse.json(response, { status: 201 });
    }

    const target = allObservations.find(
      (o) => o.kind === body.target_ref.kind && o.id === body.target_ref.id,
    );
    if (!target) return apiError(404, "not_found", "그 기억을 찾지 못했어요");

    // 🚨 하드 삭제가 아니다 — `status` 를 내린다. 행이 남아야 제안의 source_refs 가 안 끊긴다.
    const status = body.verdict === "once_only" ? "stand_alone" : "inactive";
    correctedObservations.set(`${target.kind}:${target.id}`, status);

    const response: CorrectionResponse = {
      correction: {
        id: `cr_${Date.now()}`,
        verdict: body.verdict,
        created_at: new Date().toISOString(),
      },
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
 * `need_more_observation` 은 "아직 확정하지 말고 더 지켜보자" 라서 **한 단계만** 내린다 —
 * v1 에서 `once_only` 가 하던 자리와 같다.
 */
function nextAffinity(target: Affinity, verdict: CorrectionRequest["verdict"]): Affinity {
  if (verdict === "need_more_observation" || verdict === "once_only") {
    return target.state === "confirmed" ? { ...target, state: "candidate" } : target;
  }
  return { ...target, state: "archived" };
}
