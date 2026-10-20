import { http, HttpResponse } from "msw";

import {
  draftEvent,
  generalSuggestions,
  mealDraft,
  staleSuggestion,
  suggestionDraft,
  suggestionAllergens,
  suggestionGroups,
  suggestions,
} from "../fixtures";
import { currentScenario } from "../scenario";
import type {
  Agent,
  ApproveSuggestionsRequest,
  ApproveSuggestionsResponse,
  Precheck,
} from "@/lib/api/types";
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

/**
 * 채택된 제안. 🚨 **일정과 다른 축이다** — 여기 있다고 캘린더에 들어간 것이 아니다.
 *    실서버는 `suggestion.status` 한 칸이고, 목은 그 칸을 이 집합으로 흉내 낸다.
 */
const approvedSuggestions = new Set<string>();

/** 테스트용. 목 서버는 프로세스 수명만큼 살아 있다. */
export function resetSubmittedEvents(): void {
  linkedSuggestions.clear();
  approvedSuggestions.clear();
}

/**
 * 고를 수 있는 제안 — 개인화와 일반 둘 다다 (#509). 실서버의 채택 · 초안 · 제출은 `kind` 를 보지 않는다.
 */
const pickable = [...suggestions, ...generalSuggestions];

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
  return !(suggestionAllergens[item.id] ?? []).some((a) => allergyStatus(a) === "active");
}

/**
 * 알레르기 사전검사. 🚨 **규칙이 만든다** — 모델은 관여하지 않는다 (최상위 §3).
 *
 * 🚨 **기준은 제안의 `allergens` 다** (#233 · 문서 §3-8). `agent` 와 무관해서 놀이 제안의
 *    간식도 걸린다. `health_safety` 에 행이 없는 항목만 묻는다 — `retracted` 는 묻지 않고,
 *    `active` 는 `notBlocked` 에서 제안째 빠졌다. 개인화 · 일반 공통이다 (#509).
 * 🚨 **어느 제안 것인지 `suggestion_id` 로 말한다.** 안 실으면 화면은 재료 하나로 고른 것
 *    전부를 막는다 — 알레르기에서 덜 막는 쪽으로 기울 수 없기 때문이다 (`Precheck` 의 ⚠️).
 * 🚨 **채택할 때 묻는다.** 초안을 만든 뒤가 아니다 — 일정을 안 만들면 영영 안 묻게 된다.
 */
function prechecksFor(items: Array<{ id: string }>): Precheck[] {
  return items.flatMap((s) =>
    (suggestionAllergens[s.id] ?? [])
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
    if (scenario === "scarcity" || scenario === "empty") {
      // 🚨 등록된 알레르기(`active`)가 든 일반 추천은 빼고 내보낸다 (`notBlocked`). 사전검사만 거르면
      //    확인 시트에서 "있어요" 로 등록한 재료가 다음 화면에서 다시 서고, 이번에는 묻지도 않고 채택된다.
      const general = (scenario === "empty" ? generalSuggestions : [generalSuggestions[0]]).filter(
        notBlocked,
      );
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
        // 카드의 기록 수는 그 Agent 도메인 수다 (#433 리뷰 ⑥) — 질문 카드의 scarcity.count 와 같은 값을 싣는다
        general: withStatus(
          general.map((g) => ({ ...g, record_count: scenario === "empty" ? 0 : 1 })),
        ),
        looked_at: "",
        guards: [],
        scarcity: {
          count: scenario === "empty" ? 0 : 1,
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
            deeplink: "settings/health-safety",
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

    for (const id of ids) approvedSuggestions.add(id);

    const response: ApproveSuggestionsResponse = {
      // 🚨 바뀐 행을 그대로 돌려준다 — 화면이 `status` 를 지어내지 않는다.
      suggestions: picked.map((s) => ({ ...s, status: "approved" as const })),
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
        event: { title: string; starts_at: string | null; all_day: boolean };
        items: Array<{ item_id: string | null; item_name: string }>;
        suggestion_ids?: string[];
      };

      // 🚨 일자 없이 제출되면 안 된다. 화면이 막지만 계약도 막는다 (규칙은 코드가 진다).
      //    실서버는 스키마가 `starts_at` 을 필수로 걸어 공통 검증 오류로 낸다.
      if (!body.event.starts_at) {
        return apiError(400, "validation_failed", "요청 형식이 올바르지 않아요", {
          fields: ["event.starts_at"],
        });
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
      const event = draftEvent({
        title: body.event.title,
        starts_at: body.event.starts_at,
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
      });

      // 🚨 제안의 `status` 를 싣지 않는다 — 제출은 상태를 바꾸지 않는다 (#206).
      // 201 은 실서버와 같다 (#241). 같은 키 재시도도 `withIdempotency` 가 이 코드 그대로 재생한다.
      return HttpResponse.json({ event }, { status: 201 });
    }),
  ),
];
