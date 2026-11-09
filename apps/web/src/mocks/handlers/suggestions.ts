import { http, HttpResponse } from "msw";

import {
  draftEvent,
  eventGeneralSuggestion,
  eventSuggestion,
  generalSuggestions,
  mealDraft,
  placeGeneralSuggestion,
  placeSuggestion,
  requestedFoodSuggestion,
  requestedGeneralSuggestion,
  seriesRef,
  staleSuggestion,
  suggestionDraft,
  suggestionAllergens,
  suggestionGroups,
  suggestions,
  warnedGeneralSuggestion,
  warnedSuggestion,
} from "../fixtures";
import { currentScenario } from "../scenario";
import type {
  Agent,
  ApproveSuggestionsRequest,
  ApproveSuggestionsResponse,
  Precheck,
  Reminder,
} from "@/lib/api/types";
import { isReminderLead, refuseReminder } from "./calendar";
import { allergyStatus } from "./children";
import { apiError, consentRequired, networkDelay, url } from "./helpers";
import { withIdempotency } from "./idempotency";

/**
 * 일정에 연결된 제안. 실서버의 제안 ↔ 일정 연결을 흉내 낸다 (#206) — 🚨 제안의 `status` 가 아니다.
 * 🚨 **같은 키 재시도는 `withIdempotency` 가 먼저 가로채** 처음 응답을 재생한다.
 *    여기 있다고 무조건 409 를 주면 안 된다 — 이 목록은 **새 요청으로 같은 초안을 또 내는 경우**를
 *    가릴 때만 쓴다.
 */
const linkedSuggestions = new Set<string>();

/** 새로 넣은 일정의 번호. 넣을 때마다 다른 id 를 주려고 센다 (#723). */
let submittedCount = 0;

/**
 * 채택된 제안. 🚨 **일정과 다른 축이다** — 여기 있다고 캘린더에 들어간 것이 아니다.
 *    실서버는 `suggestion.status` 한 칸이고, 목은 그 칸을 이 집합으로 흉내 낸다.
 */
const approvedSuggestions = new Set<string>();

/** 테스트용. 목 서버는 프로세스 수명만큼 살아 있다. */
export function resetSubmittedEvents(): void {
  linkedSuggestions.clear();
  submittedCount = 0;
  approvedSuggestions.clear();
}

/**
 * 고를 수 있는 제안 — 개인화와 일반 둘 다다 (#509). 실서버의 채택 · 초안 · 제출은 `kind` 를 보지 않는다.
 */
const pickable = [
  ...suggestions,
  ...generalSuggestions,
  requestedGeneralSuggestion,
  requestedFoodSuggestion,
  placeSuggestion,
  placeGeneralSuggestion,
  eventSuggestion,
  eventGeneralSuggestion,
];

/**
 * 초안 만들기와 제출이 함께 거는 검사. 🚨 **실서버(#241)와 같은 순서 · 같은 코드 · 같은 문구다** —
 *    없는 제안 404 → 채택 안 된 제안 422. 화면은 이 경로의 `message` 를 그대로 띄운다.
 * 🚨 **모르는 id 를 조용히 걸러 내지 않는다.** 화면에 없는 제안이 섞였다는 뜻이라 버그다.
 */
function refuseUnapproved(ids: string[]) {
  for (const id of ids) {
    if (!pickable.some((s) => s.id === id)) {
      return apiError(404, "not_found", "제안을 찾을 수 없어요");
    }
    if (!approvedSuggestions.has(id)) {
      return apiError(422, "not_approved", "채택되지 않은 제안이 포함돼 있어요");
    }
  }
  return null;
}

/**
 * 🚨 **이미 알레르기가 등록된(`active`) 항목이 든 제안은 목록에서 뺀다** (문서 §3-8 ①). 개인화 · 일반 공통이다 —
 *    실서버도 `kind` 를 가리지 않고 `check.blocked` 면 뺀다.
 */
function notBlocked(item: { id: string }): boolean {
  return !allergensOf(item.id).some((a) => allergyStatus(a) === "active");
}

/**
 * 제안에 든 알레르기 항목. `precheck_shared_item` 이면 두부 제안(`s_2`)에도 계란이 들어 있다 — 같은 식품이 든
 * 제안 둘을 고르면 시트가 그 식품을 한 장으로 묻는지 보는 시나리오다 (#823). 픽스처를 바꾸지 않는 것은
 * `s_2` 를 골라 다음 단계로 가는 e2e 가 여럿이라서다.
 */
function allergensOf(id: string): string[] {
  const base = suggestionAllergens[id] ?? [];
  return currentScenario() === "precheck_shared_item" && id === "s_2" ? [...base, "계란"] : base;
}

/**
 * 알레르기 사전검사. 🚨 **규칙이 만든다** — 모델은 관여하지 않는다 (최상위 §3).
 *
 * 🚨 **기준은 제안의 `allergens` 다** (#233 · 문서 §3-8). `agent` 와 무관해서 놀이 제안의
 *    간식도 걸린다. `health_safety` 에 걸리는 행이 없는 항목만 묻는다 — 괜찮았던 식품(`tolerated`)은 묻지 않고,
 *    `active` 는 `notBlocked` 에서 제안째 빠졌다. 🚨 **내린 기록은 다시 묻는다** (#825 · 서버 PR 3 ·
 *    `allergyStatus` 머리말). 개인화 · 일반 공통이다 (#509).
 * 🚨 **어느 제안 것인지 `suggestion_id` 로 말한다.** 안 실으면 화면은 재료 하나로 고른 것
 *    전부를 막는다 — 알레르기에서 덜 막는 쪽으로 기울 수 없기 때문이다 (`Precheck` 의 ⚠️).
 * 🚨 **채택할 때 묻는다.** 초안을 만든 뒤가 아니다 — 일정을 안 만들면 영영 안 묻게 된다.
 */
function prechecksFor(items: Array<{ id: string }>): Precheck[] {
  return items.flatMap((s) =>
    allergensOf(s.id)
      .filter((a) => allergyStatus(a) === null)
      .map((a) => ({
        code: "unknown_ingredient" as const,
        item: a,
        note: "알레르기 기록에 없음",
        suggestion_id: s.id,
      })),
  );
}

/** 05 제안 · 06 승인. */
export const suggestionHandlers = [
  http.post(url("/children/:cid/suggestions"), async ({ request }) => {
    // Agent 호출이라 홈보다 느리다. 진행 오버레이가 실제로 보이는 시간.
    await networkDelay(900);
    const scenario = currentScenario();
    const asked = (await request.json().catch(() => ({}))) as {
      agents?: Agent[];
      run_id?: string;
    };
    /**
     * 🚨 **run_id 로 들어오면 그 run 의 채택한 추천도 돌아온다** (#249). 새로고침해도 채택이 남는다 —
     *    화면은 `status` 로 되살리고, 다시 고르게 그리면 채택 API 가 `not_draft` 로 거절한다.
     *    run_id 가 없으면(홈 · 04 제안) 새로 만든 추천이라 늘 `draft` 다.
     */
    const withStatus = <T extends { id: string; status: string }>(items: T[]): T[] =>
      asked.run_id
        ? items.map((s) =>
            approvedSuggestions.has(s.id) ? { ...s, status: "approved" as const } : s,
          )
        : items;

    if (scenario === "consent") return consentRequired("child_health");
    // 🚨 05 도 하루 한도에 걸린다 — 요청마다 Agent 를 돌려서다. 다시 눌러도 같은 답이라 화면이 다시 시도를 세우지 않는다
    if (scenario === "daily_limit") {
      return apiError(
        429,
        "daily_input_limit",
        "오늘은 더 준비할 수 없어요. 내일 다시 골라 주세요.",
      );
    }

    // 🚨 근거가 없으면 개인화 대신 **일반 추천**이다. 되묻는 질문은 배열이 아니라 단수 — 한 개까지만.
    //    개인화(`suggestions`)와 일반(`general`)은 **다른 필드**다. 한 배열에 섞으면 언젠가
    //    근거 0건인 것이 개인화로 집계된다 (CLAUDE.md §2). 일반 추천의 근거는 **문서 행**이다 (#249).
    //    결과를 낸 Agent 는 묶음이 온다 — 일반 추천만 있어도 그 Agent 의 묶음이 있다.
    //    ⚠️ 답하는 길(#246) 전이라 `options` 는 비어 온다 — 서버와 같은 모양이다.
    //    `suggest_requested` 는 보호자가 놀이를 정해 물은 일반 추천이다 — 문서 근거가 비어 온다 (#597).
    //    `suggest_requested_food` 는 재료를 정해 물은 식사 일반 추천이다 — 같은 꼴로 문서 · 메뉴 근거가 비어 온다 (#740).
    if (
      scenario === "scarcity" ||
      scenario === "empty" ||
      scenario === "suggest_requested" ||
      scenario === "suggest_requested_food"
    ) {
      // 🚨 등록된 알레르기(`active`)가 든 일반 추천은 빼고 내보낸다 (`notBlocked`). 사전검사만 거르면
      //    확인 시트에서 "있어요" 로 등록한 재료가 다음 화면에서 다시 서고, 이번에는 묻지도 않고 채택된다.
      const general = (
        scenario === "empty"
          ? generalSuggestions
          : scenario === "suggest_requested"
            ? [requestedGeneralSuggestion]
            : scenario === "suggest_requested_food"
              ? [requestedFoodSuggestion]
              : [generalSuggestions[0]]
      ).filter(notBlocked);
      const count = scenario === "scarcity" ? 1 : 0;
      const generalAgents = new Set(general.map((g) => g.agent));
      return HttpResponse.json({
        suggestions: [],
        // 요청한 Agent 마다 묶음이 온다 — 일반 추천을 낸 쪽은 안내 없이, 못 낸 쪽은 0개 안내와 함께
        groups: (asked.agents ?? [...generalAgents]).map((agent) => ({
          agent,
          merges_into_one: agent === "food",
          notice: generalAgents.has(agent)
            ? null
            : { code: "empty", message: "조건에 맞는 추천이 없어요." },
          cautions: [],
        })),
        // 카드의 기록 수는 그 Agent 도메인 수다 (#433 리뷰 ⑥). 단일 Agent 목이라 합계와 우연히 같다.
        general: withStatus(general.map((g) => ({ ...g, record_count: count }))),
        looked_at: "",
        guards: [],
        scarcity: {
          count,
          question: {
            id: "q1",
            text: "요즘 실내와 야외 중 어디를 더 찾나요?",
            options: [],
          },
        },
        // 🚨 일반 추천도 고르고 채택하므로 알레르기 사전검사가 걸린다 (#509 · 게이트 ㉡)
        prechecks: prechecksFor(general),
      });
    }

    // Agent 둘이 모두 일반 추천을 낸 응답. `scarcity.count`는 호환용 합계이고 화면은 각 카드의
    // `record_count`를 응답 Agent 순서대로 따로 말해야 한다 (#662).
    if (scenario === "suggest_multi_general") {
      const recordCounts: Partial<Record<Agent, number>> = { food: 22, activity: 8 };
      const order = asked.agents ?? ["food", "activity"];
      const general = order.flatMap((agent) => {
        const item = generalSuggestions.find((suggestion) => suggestion.agent === agent);
        return item ? [{ ...item, record_count: recordCounts[agent] ?? 0 }] : [];
      });
      const generalAgents = new Set(general.map((item) => item.agent));
      return HttpResponse.json({
        suggestions: [],
        groups: order.map((agent) => ({
          agent,
          merges_into_one: agent === "food",
          notice: generalAgents.has(agent)
            ? null
            : { code: "empty", message: "조건에 맞는 추천이 없어요." },
          cautions: [],
        })),
        general: withStatus(general),
        looked_at: "",
        guards: [],
        scarcity: { count: 30, question: null },
        prechecks: prechecksFor(general),
      });
    }

    // 🚨 아래 둘은 **실패가 아니다** (#249). 결과를 낸 Agent 는 0개여도 안내와 함께 묶음이 오고,
    //    이어서 할 수 없는 Agent 도 안내만 온다. 묶음이 없는 Agent 만 실패다.
    if (scenario === "suggest_counts") {
      return HttpResponse.json({
        suggestions: suggestions.filter((s) => s.agent === "food").slice(0, 2),
        groups: [
          {
            agent: "food",
            merges_into_one: true,
            notice: { code: "fewer", message: "조건에 맞는 추천을 2개 준비했어요." },
            cautions: [],
          },
          {
            agent: "activity",
            merges_into_one: false,
            notice: { code: "empty", message: "조건에 맞는 추천이 없어요." },
            cautions: [],
          },
        ],
        general: [],
        looked_at: "",
        guards: [],
        scarcity: null,
        prechecks: [],
      });
    }
    if (scenario === "cannot_resume") {
      return HttpResponse.json({
        suggestions: withStatus(suggestions.filter((s) => s.agent === "food")),
        groups: [
          { agent: "food", merges_into_one: true, notice: null, cautions: [] },
          {
            agent: "activity",
            merges_into_one: false,
            notice: {
              code: "cannot_resume",
              message: "이전 요청을 이어서 준비할 수 없어요. 홈에서 다시 골라 주세요.",
            },
            cautions: [],
          },
        ],
        general: [],
        looked_at: "",
        guards: [],
        scarcity: null,
        prechecks: [],
      });
    }

    // 알레르기 정보를 모르면 Food Agent 를 아예 실행하지 않는다 (기본값으로 넘기지 않는다).
    // 남은 activity 제안의 근거는 6개월이 지났다 — 점선 근거 칩이 나오는 유일한 경로다 (NF-08).
    if (scenario === "stale") {
      return HttpResponse.json({
        suggestions: [staleSuggestion],
        // 묶음 머리말은 살아 있는 Agent 것만 온다 — 여기선 food 가 guard 에 막혀 activity 하나다.
        groups: suggestionGroups.filter((g) => g.agent === staleSuggestion.agent),
        looked_at: "오늘 급식 · 확정 관심 0건",
        guards: [
          {
            code: "safety_unknown",
            blocked_agents: ["food"],
            message: "알레르기 정보가 없어 식사 제안을 안전하게 걸러낼 수 없어요",
            deeplink: "profile",
          },
        ],
        scarcity: null,
      });
    }

    // partial 이면 실패한 Agent 의 제안은 빠진 채로 온다 — 묶음 머리말도 같이 빠진다.
    // 🚨 추천이 나간 뒤 보호자가 확인 시트에서 "있어요" 를 저장한 제안은 여기서 빠진다 (`notBlocked`).
    const alive = (
      scenario === "partial" ? suggestions.filter((s) => s.agent === "food") : suggestions
    ).filter(notBlocked);
    const prechecks = prechecksFor(alive);

    /**
     * 🚨 **같은 Agent 안에서 개인화와 일반이 섞여 온다** (#512 리뷰). Agent 가 후보마다 아이 기록 근거로
     *    `kind` 를 정해서(서버 `build()`), 식사 개인화 셋과 식사 일반 하나가 한 응답에 실린다. 일반 추천이
     *    있으니 `scarcity` 도 붙는다. 놀이는 0개 안내다.
     *    서버는 식사면 `kind` 상관없이 초안 한 장으로 묶는다 — 화면의 묶음 안내도 둘을 합쳐 세야 한다.
     */
    if (scenario === "suggest_mixed") {
      const food = alive.filter((s) => s.agent === "food");
      const general = generalSuggestions.filter((g) => g.agent === "food").filter(notBlocked);
      return HttpResponse.json({
        suggestions: withStatus(food),
        groups: (asked.agents ?? ["food"]).map((agent) =>
          agent === "food"
            ? suggestionGroups[0]
            : {
                agent,
                merges_into_one: false,
                notice: { code: "empty", message: "조건에 맞는 추천이 없어요." },
                cautions: [],
              },
        ),
        general: withStatus(general.map((g) => ({ ...g, record_count: 3 }))),
        looked_at: "",
        guards: [],
        scarcity: { count: 3, question: null },
        prechecks: prechecksFor([...food, ...general]),
      });
    }
    if (scenario === "precheck_unknown_code") {
      // 🚨 이름은 지어낸 것이다 — 화면이 **아는 code 가 아니라는 것**만 뜻이 있다.
      //    재료가 없는 물놀이 제안에 붙여서, 알레르기 질문이 섞이지 않게 한다.
      const water = alive.find(
        (s) => (suggestionAllergens[s.id] ?? []).length === 0 && s.agent === "activity",
      );
      if (water) {
        prechecks.push({
          code: "not_yet_known_check",
          item: "물놀이장 이용 조건",
          note: "새 검사",
          suggestion_id: water.id,
        });
      }
    }

    /**
     * 장소가 붙은 놀이 추천 (#516). 개인화 하나(실내 물놀이장 자리)와 일반 하나에 확인 문구(`place_notice`)가 온다.
     * 🚨 문구는 근거가 아니라서 `evidence` 는 그대로다 — 화면이 "사용한 기록" 에 넣거나 세면 안 된다.
     * 일반 추천이 있으니 `scarcity` 도 붙는다(`suggest_mixed` 와 같은 모양).
     */
    if (scenario === "suggest_place") {
      const shown = alive.map((s) => (s.id === "s_4" ? placeSuggestion : s));
      const general = [placeGeneralSuggestion].filter(notBlocked);
      return HttpResponse.json({
        suggestions: withStatus(shown),
        groups: suggestionGroups.filter((g) => shown.some((s) => s.agent === g.agent)),
        general: withStatus(general),
        looked_at: "",
        guards: [],
        scarcity: { count: 3, question: null },
        prechecks: prechecksFor([...shown, ...general]),
      });
    }

    /**
     * 행사가 붙은 놀이 추천 (#681). 개인화 하나(여러 날 행사)와 일반 하나(하루짜리 행사)에 기간
     * (`event_starts_on` · `event_ends_on`)과 행사 확인 문구가 온다. `suggest_place` 와 같은 자리를 바꾼다.
     * 🚨 기간도 근거가 아니라서 `evidence` 는 그대로다.
     */
    if (scenario === "suggest_event") {
      const shown = alive.map((s) => (s.id === "s_4" ? eventSuggestion : s));
      const general = [eventGeneralSuggestion].filter(notBlocked);
      return HttpResponse.json({
        suggestions: withStatus(shown),
        groups: suggestionGroups.filter((g) => shown.some((s) => s.agent === g.agent)),
        general: withStatus(general),
        looked_at: "",
        guards: [],
        scarcity: { count: 3, question: null },
        prechecks: prechecksFor([...shown, ...general]),
      });
    }

    /**
     * 위험 경고가 붙은 놀이 추천 (#402 · #672). 개인화 하나(실내 물놀이장 자리)에 경고 하나, 일반 하나에 경고 둘.
     * 🚨 경고는 근거가 아니라서 `evidence` 는 그대로다. 나머지 추천은 `warnings: []` 로 온다 — 빈 줄이 서면 안 된다.
     */
    if (scenario === "suggest_warnings") {
      const shown = alive.map((s) => (s.id === "s_4" ? warnedSuggestion : s));
      const general = [warnedGeneralSuggestion].filter(notBlocked);
      return HttpResponse.json({
        suggestions: withStatus(shown),
        groups: suggestionGroups.filter((g) => shown.some((s) => s.agent === g.agent)),
        general: withStatus(general),
        looked_at: "",
        guards: [],
        scarcity: { count: 3, question: null },
        prechecks: prechecksFor([...shown, ...general]),
      });
    }

    // 식품 사전에 없는 알레르기(꽃가루)는 뺄 놀이를 코드가 못 정해서 확인 문구만 낸다 (#433 리뷰 ③).
    // 글자는 서버 activity/tools/filters.allergy_cautions 와 같다
    if (scenario === "suggest_cautions") {
      return HttpResponse.json({
        suggestions: withStatus(alive),
        groups: suggestionGroups
          .filter((g) => alive.some((s) => s.agent === g.agent))
          .map((g) =>
            g.agent === "activity"
              ? {
                  ...g,
                  cautions: [
                    "등록된 알레르기(꽃가루)가 있어요. 장소랑 재료를 한 번 확인해 주세요.",
                  ],
                }
              : g,
          ),
        looked_at: "",
        guards: [],
        scarcity: null,
        prechecks,
      });
    }

    return HttpResponse.json({
      suggestions: withStatus(alive),
      groups: suggestionGroups.filter((g) => alive.some((s) => s.agent === g.agent)),
      looked_at: "오늘 급식 · 최근 3일 식사 · 확정 관심 2건",
      guards: [],
      scarcity: null,
      prechecks,
    });
  }),

  // 되묻는 질문의 답. 관찰 1건으로 저장된다.
  http.post(url("/children/:cid/answers"), async () => {
    await networkDelay();
    return HttpResponse.json({ saved: true });
  }),

  /**
   * 고른 제안을 **채택한다** (`status: approved`).
   *
   * 🚨 **캘린더에는 아무것도 안 들어간다.** 일정으로 만들지는 다음 단계가 따로 묻는다 —
   *    두 축을 한 호출로 묶으면 "이걸로 할 건데 캘린더엔 안 넣을래" 를 표현할 방법이 없어진다.
   * 🚨 **모르는 id 는 조용히 넘기지 않는다** — 화면이 안 보이는 제안을 채택했다는 뜻이라 버그다.
   *
   * ⚠️ 경로가 계약서에 없다 (`types.ts` 의 `ApproveSuggestionsRequest` 참고 · #151).
   */
  http.post(url("/children/:cid/suggestions/approve"), async ({ request }) => {
    await networkDelay(400);
    if (currentScenario() === "consent") return consentRequired("child_health");

    const body = (await request.json()) as ApproveSuggestionsRequest;
    const ids = [...new Set(body.suggestion_ids ?? [])];
    if (ids.length === 0) return apiError(400, "validation_failed", "제안을 선택해 주세요");

    const picked = pickable.filter((s) => ids.includes(s.id));
    if (picked.length !== ids.length) {
      return apiError(404, "not_found", "제안을 찾을 수 없어요");
    }
    // ⚠️ 실서버의 `409 not_draft`(거절된 제안) · `409 suggestion_expired`(24시간 지남)는 여기서
    //    안 난다 — 목의 제안은 늘 `draft` 이고 만료되지 않는다. 이미 채택한 것은 서버도 그대로 200 이다.

    /**
     * 🚨 **채택할 때 알레르기를 다시 읽는다** (문서 §3-8 ②). 목록을 받은 뒤 다른 보호자가
     *    등록한 알레르기가 든 제안이 있으면 **하나라도 걸려도 전부 거절**하고, 걸린 id 를 싣는다.
     *    이미 채택한 것은 다시 보지 않는다(`draft` 만 본다).
     * ⚠️ **실서버에는 아직 없다** — 서버 PR 2(현식님)가 이 모양으로 낼 예정이다(잠정 · 열린 결정 8).
     *    지금 실서버는 그대로 채택한다.
     */
    const blocked = picked
      .filter((s) => !approvedSuggestions.has(s.id) && !notBlocked(s))
      .map((s) => s.id);
    if (blocked.length > 0) {
      return apiError(
        409,
        "allergen_blocked",
        "알레르기로 등록된 식품이 들어 있어 고를 수 없어요",
        {
          suggestion_ids: blocked,
        },
      );
    }

    for (const id of ids) approvedSuggestions.add(id);

    const response: ApproveSuggestionsResponse = {
      // 🚨 바뀐 행을 그대로 돌려준다 — 화면이 `status` 를 지어내지 않는다.
      // 🚨 **계약 모양으로 줄인다** (`docs/api/suggestions-v1.md` §7 · #554). 실서버 채택 응답은 근거를 읽지
      //    않아 개인화도 `evidence: []` 이고, 일반 추천에 `basis` · `record_count` 가 없다. 목이 행을 통째로
      //    돌려주면 실서버에 없는 필드가 목에서만 차 있게 된다 — 화면이 기대는 공통 필드만 싣는다.
      suggestions: picked.map((s) => ({
        id: s.id,
        child_id: s.child_id,
        agent: s.agent,
        content: s.content,
        status: "approved" as const,
      })),
    };
    return HttpResponse.json(response, { status: 200 });
  }),

  /**
   * 채택한 제안들 → 일정 초안. 🚨 **아무것도 쓰지 않는다** (#121) — 저장은 제출
   * (`POST /children/{cid}/events`) 하나뿐이고 그게 승인 게이트 ㉠ 이다.
   *
   * 🚨 **`food` 는 한 끼로 묶는다.** 고른 개수와 초안 개수가 1:1 이 아니다 — 저녁 반찬 제안
   *    두 건을 고르면 "저녁 식사" 일정 **한 건**이 된다. 묶는 판단은 도메인 지식이라
   *    화면이 하지 않고 여기서 한다 (실제 서버도 그래야 한다).
   *
   * ⚠️ 경로가 계약서에 없다 (`types.ts` 의 `CreateEventDraftsRequest` 참고 · #151).
   */
  http.post(url("/children/:cid/suggestions/event-drafts"), async ({ request }) => {
    await networkDelay();
    const body = (await request.json()) as { suggestion_ids: string[] };
    const ids = [...new Set(body.suggestion_ids)];
    if (ids.length === 0) return apiError(400, "validation_failed", "제안을 선택해 주세요");

    // 🚨 **채택한 것만 일정이 된다** — 고르기와 일정 만들기는 다른 단계다 (#151).
    const refused = refuseUnapproved(ids);
    if (refused) return refused;

    const pool = [...pickable, staleSuggestion];
    const chosen = ids
      .map((id) => pool.find((s) => s.id === id))
      .filter((s): s is (typeof pool)[number] => s !== undefined);

    const food = chosen.filter((s) => s.agent === "food");
    const rest = chosen.filter((s) => s.agent !== "food");

    const drafts = [
      // 🚨 식사는 한 장으로 묶인다. 묶인 장은 `suggestion_ids` 가 여러 개다 —
      //    제출하면 그 제안들이 **전부** 이 일정에 연결된다.
      ...(food.length > 0 ? [mealDraft(food)] : []),
      ...rest.map((s) => suggestionDraft(s)),
    ];

    return HttpResponse.json(
      {
        drafts,
      },
      { status: 201 },
    );
  }),

  // 🚨 알레르기 · 건강 기록(`/health-safety`, 게이트 ㉡)은 `handlers/children.ts` 하나가 갖는다 (#374).
  //    여기 두 번째 벌이 있었는데, 먼저 등록된 children 쪽에 가려 닿지 않으면서 409 를 다른 코드
  //    (`conflict`)로 내고 있었다 — 등록 순서가 바뀌는 순간 화면이 409 를 실패로 그린다.

  // 🚨 승인 게이트 ㉠ — 되돌릴 수 없는 지점. 캘린더에 쓰는 것은 여기 하나다.
  http.post(
    url("/children/:cid/events"),
    withIdempotency(async ({ request }) => {
      await networkDelay();
      const body = (await request.json()) as {
        event: {
          title: string;
          starts_at: string | null;
          ends_at?: string | null;
          all_day: boolean;
        };
        items: Array<{ item_id: string | null; item_name: string }>;
        suggestion_ids?: string[];
        reminder?: { lead: unknown } | null;
        repeat?: { weekdays?: number[]; until?: string; label?: string };
      };

      // 🚨 반복 일정(#738)은 요일과 끝 날짜가 다 있어야 한다. 문구는 서버 값이라 되돌려 받지 않는다 (`RepeatRequest`).
      //    회차로 펼치는 것은 실서버 몫이라(#761) 목은 첫 회차 한 건만 돌려준다 — 서버 응답도 `event` 는 첫 회차고 `series` 가 붙는다.
      if (
        body.repeat &&
        (!body.repeat.weekdays?.length || !body.repeat.until || "label" in body.repeat)
      ) {
        return apiError(400, "validation_failed", "요청 형식이 올바르지 않아요", {
          fields: ["repeat"],
        });
      }
      // 🚨 반복 규칙이 성립하지 않으면 서버는 422 `invalid_repeat` 다 (#761). 카드에서 고칠 수 없는 실패라
      //    화면이 "대화로 다시 말해 주세요" 를 붙이는지 보려고 시나리오로 만든다.
      if (body.repeat && currentScenario() === "repeat_invalid") {
        return apiError(422, "invalid_repeat", "반복은 첫 회차부터 90일까지만 돼요");
      }

      // 🚨 일자 없이 제출되면 안 된다. 화면이 막지만 계약도 막는다 (규칙은 코드가 진다).
      //    실서버는 스키마가 `starts_at` 을 필수로 걸어 공통 검증 오류로 낸다.
      if (!body.event.starts_at) {
        return apiError(400, "validation_failed", "요청 형식이 올바르지 않아요", {
          fields: ["event.starts_at"],
        });
      }

      // 실서버 `SubmitEventBody._ends_after_starts` 와 같다. 08 시트가 읽은 끝을 실어 보낸다 (#723).
      // 모델 검증이라 실서버는 공통 검증 오류(400 · `fields: ["event"]`)로 낸다 (`api/errors.py`).
      const endsAt = body.event.ends_at ?? null;
      if (endsAt !== null && Date.parse(endsAt) <= Date.parse(body.event.starts_at)) {
        return apiError(400, "validation_failed", "요청 형식이 올바르지 않아요", {
          fields: ["event"],
        });
      }

      // 🚨 알림이 틀리면 **일정도 넣지 않는다** — 서버는 저장 전에 검사한다 (#603).
      let reminder: Reminder | null = null;
      if (body.reminder) {
        const { lead } = body.reminder;
        if (!isReminderLead(lead)) {
          return apiError(400, "validation_failed", "요청 형식이 올바르지 않아요", {
            fields: ["reminder.lead"],
          });
        }
        // 🚨 반복 일정은 첫 회차 하나로 거절하지 않는다 (#761). 서버는 지난 회차만 건너뛰고 전 회차가 다 지났을 때만
        //    422 다 — 목은 회차를 펼치지 않아서 반복이면 시각 검사를 하지 않는다 (종류 검사는 위에서 했다).
        if (!body.repeat) {
          const wrong = refuseReminder(lead, body.event.starts_at, body.event.all_day);
          if (wrong) return wrong;
        }
        reminder = { lead };
      }

      const sids = [...new Set(body.suggestion_ids ?? [])];
      const refused = refuseUnapproved(sids);
      if (refused) return refused;

      // 여기까지 왔다는 건 처음 보는 키라는 뜻이다 — 즉 새 요청이다.
      // 🚨 묶인 초안은 **하나라도** 이미 연결돼 있으면 막는다. 일부만 넣으면 같은 제안이 일정 둘에 걸린다.
      if (sids.some((sid) => linkedSuggestions.has(sid))) {
        return apiError(409, "already_confirmed", "이미 캘린더에 넣은 제안이에요");
      }
      for (const sid of sids) linkedSuggestions.add(sid);

      /**
       * 🚨 **보호자가 확인한 값을 그대로 돌려준다.** 제목이나 시각이 바뀌어 돌아오면
       *    "확인하고 넣은 것" 과 "들어간 것" 이 달라져 승인 게이트가 거짓말이 된다.
       * 🚨 `items` 는 **최종 목록**이다 — 배열에서 빠진 것은 저장되지 않는다 (#122).
       */
      submittedCount += 1;
      const event = draftEvent({
        // 🚨 넣을 때마다 다른 id 다 — 08 은 이 id 를 공지 항목에 잇는데, 같으면 "한 일정을 두 항목에" 로 막힌다.
        id: `e_new_${submittedCount}`,
        title: body.event.title,
        starts_at: body.event.starts_at,
        ends_at: endsAt,
        all_day: body.event.all_day,
        // 실서버와 같다 (#241) — 제안에서 온 일정만 `agent` 이고, 출처 참조는 이 경로가 채우지 않는다.
        created_by: sids.length > 0 ? "agent" : "caregiver",
        source_refs: [],
        items: body.items.map((item, index) => ({
          item_id: item.item_id ?? `i_new_${index}`,
          item_name: item.item_name,
          is_prepared: false,
          prepared_at: null,
        })),
        // 🚨 제출한 보호자 본인의 알림만 걸린다 — 응답도 그것만 싣는다.
        reminder,
        // 반복이면 첫 회차에 묶음이 붙는다 (서버 #761). 반복이 아니면 키 자체가 없다.
        ...(body.repeat?.weekdays && body.repeat.until
          ? {
              series: seriesRef(
                `series_new_${submittedCount}`,
                body.repeat.weekdays,
                body.repeat.until,
              ),
            }
          : {}),
      });

      // 🚨 제안의 `status` 를 싣지 않는다 — 제출은 상태를 바꾸지 않는다 (#206).
      // 201 은 실서버와 같다 (#241). 같은 키 재시도도 `withIdempotency` 가 이 코드 그대로 재생한다.
      return HttpResponse.json({ event }, { status: 201 });
    }),
  ),
];
