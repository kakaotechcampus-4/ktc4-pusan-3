import { http, HttpResponse } from "msw";

import {
  draftEvent,
  generalSuggestions,
  healthSafety,
  mealDraft,
  staleSuggestion,
  suggestionDraft,
  suggestionGroups,
  suggestions,
} from "../fixtures";
import { currentScenario } from "../scenario";
import type { ApproveSuggestionsRequest, ApproveSuggestionsResponse } from "@/lib/api/types";
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
 * 초안 만들기와 제출이 함께 거는 검사. 🚨 **실서버(#241)와 같은 순서 · 같은 코드 · 같은 문구다** —
 *    없는 제안 404 → 채택 안 된 제안 422. 화면은 이 경로의 `message` 를 그대로 띄운다.
 * 🚨 **모르는 id 를 조용히 걸러 내지 않는다.** 화면에 없는 제안이 섞였다는 뜻이라 버그다.
 */
function refuseUnapproved(ids: string[]) {
  for (const id of ids) {
    if (!suggestions.some((s) => s.id === id)) {
      return apiError(404, "not_found", "제안을 찾을 수 없어요");
    }
    if (!approvedSuggestions.has(id)) {
      return apiError(422, "not_approved", "채택되지 않은 제안이 포함돼 있어요");
    }
  }
  return null;
}

/** 05 제안 · 06 승인. */
export const suggestionHandlers = [
  http.post(url("/children/:cid/suggestions"), async () => {
    // Agent 호출이라 홈보다 느리다. 진행 오버레이가 실제로 보이는 시간.
    await networkDelay(900);
    const scenario = currentScenario();

    if (scenario === "consent") return consentRequired("child_health");

    // 🚨 근거가 없으면 개인화 대신 **일반 추천**이다. 되묻는 질문은 배열이 아니라 단수 — 한 개까지만.
    //    개인화(`suggestions`)와 일반(`general`)은 **다른 필드**다. 한 배열에 섞으면 언젠가
    //    근거 0건인 것이 개인화로 집계된다 (CLAUDE.md §2).
    //    ⚠️ `general` 은 계약서 v1 에 아직 없다 — types.ts 의 ⚠️ 참고 (서버 Owner 협의 대상).
    if (scenario === "scarcity" || scenario === "empty") {
      return HttpResponse.json({
        suggestions: [],
        general: scenario === "empty" ? generalSuggestions : [generalSuggestions[0]],
        looked_at: "오늘 급식",
        guards: [],
        scarcity: {
          count: scenario === "empty" ? 0 : 1,
          question: {
            id: "q1",
            text: "요즘 실내와 야외 중 어디를 더 찾나요?",
            options: ["실내", "야외", "모르겠어요"],
          },
        },
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

    // partial 이면 실패한 Agent 의 제안은 빠진 채로 온다. 화면은 성공한 쪽을 그린다.
    // partial 이면 실패한 Agent 의 제안은 빠진 채로 온다 — 묶음 머리말도 같이 빠진다.
    const alive =
      scenario === "partial" ? suggestions.filter((s) => s.agent === "food") : suggestions;
    /**
     * 알레르기 사전검사. 🚨 **규칙이 만든다** — 모델은 관여하지 않는다 (최상위 §3).
     *
     * 🚨 **어느 제안 것인지 `suggestion_id` 로 말한다.** 안 실으면 화면은 재료 하나로 고른 것
     *    전부를 막는다 — 알레르기에서 덜 막는 쪽으로 기울 수 없기 때문이다 (`Precheck` 의 ⚠️).
     * 🚨 **채택할 때 묻는다.** 초안을 만든 뒤가 아니다 — 일정을 안 만들면 영영 안 묻게 된다.
     * ⚠️ 기준이 `agent === "food"` 인지 "재료가 있을 때" 인지는 서버 쪽 결정이다 (#151).
     */
    const firstFood = alive.find((s) => s.agent === "food");

    return HttpResponse.json({
      suggestions: alive,
      groups: suggestionGroups.filter((g) => alive.some((s) => s.agent === g.agent)),
      looked_at: "오늘 급식 · 최근 3일 식사 · 확정 관심 2건",
      guards: [],
      scarcity: null,
      prechecks: firstFood
        ? [
            {
              code: "unknown_ingredient",
              item: "닭고기",
              note: "첫 기록",
              suggestion_id: firstFood.id,
            },
          ]
        : [],
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

    const picked = suggestions.filter((s) => ids.includes(s.id));
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

    const pool = [...suggestions, staleSuggestion];
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

  /* ── 승인 게이트 ㉡ — 알레르기·건강 기록의 유일한 쓰기 경로 ─────────────
   * 🚨 보호자 토큰으로만 호출된다. Agent 실행 경로에는 이 함수를 부르는 코드가 없고,
   *    DB 레벨에서도 Agent/Curator role 에 write 권한이 없다 (계약서 §07 "방어가 두 겹").
   */
  http.get(url("/children/:cid/health-safety"), async () => {
    await networkDelay();
    return HttpResponse.json({ items: healthSafety });
  }),

  http.post(url("/children/:cid/health-safety"), async ({ request }) => {
    await networkDelay();
    const body = (await request.json()) as { type: string; label: string; category: string };

    // UNIQUE(child_id, type, label) — 같은 항목 재등록은 409 다. 지우고 다시 넣어야 한다.
    if (healthSafety.some((s) => s.type === body.type && s.label === body.label)) {
      return apiError(409, "conflict", "이미 등록된 항목이에요");
    }

    return HttpResponse.json(
      {
        safety: {
          ...healthSafety[0],
          id: `hs_${Date.now()}`,
          type: body.type,
          label: body.label,
          category: body.category,
          aliases: [],
          severity: null,
          reactions: [],
          notes: null,
        },
      },
      { status: 201 },
    );
  }),

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
