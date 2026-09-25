import { http, HttpResponse } from "msw";

import {
  draftEvent,
  generalSuggestions,
  healthSafety,
  staleSuggestion,
  suggestionDraft,
  suggestions,
} from "../fixtures";
import { currentScenario } from "../scenario";
import { apiError, consentRequired, networkDelay, url } from "./helpers";
import { withIdempotency } from "./idempotency";

/**
 * 제출된 일정. 🚨 **같은 키 재시도는 `withIdempotency` 가 먼저 가로채** 처음 응답을 재생한다.
 *    여기 있다고 무조건 409 를 주면 안 된다 — 이 목록은 **새 요청으로 같은 초안을 또 내는 경우**를
 *    가릴 때만 쓴다.
 */
const submittedSuggestions = new Set<string>();

/** 테스트용. 목 서버는 프로세스 수명만큼 살아 있다. */
export function resetSubmittedEvents(): void {
  submittedSuggestions.clear();
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
    return HttpResponse.json({
      suggestions: scenario === "partial" ? [suggestions[0]] : suggestions,
      looked_at: "오늘 급식 · 최근 3일 식사 · 확정 관심 2건",
      guards: [],
      scarcity: null,
    });
  }),

  // 되묻는 질문의 답. 관찰 1건으로 저장된다.
  http.post(url("/children/:cid/answers"), async () => {
    await networkDelay();
    return HttpResponse.json({ saved: true });
  }),

  /**
   * 🚨 **아무것도 쓰지 않는다** (#121). 초안과 사전검사만 내려준다 —
   *    저장은 제출(`POST /children/{cid}/events`) 하나뿐이고 그게 승인 게이트 ㉠ 이다.
   */
  http.post(url("/suggestions/:sid/event"), async ({ params }) => {
    await networkDelay();
    const sid = String(params.sid);
    const suggestion = suggestions.find((s) => s.id === sid) ?? staleSuggestion;

    return HttpResponse.json(
      {
        draft: suggestionDraft(suggestion),
        /**
         * 🚨 **`food` 제안에만 붙는다.** 사전검사는 규칙이 만들고(최상위 §3 — 알레르기 필터는
         *    코드가 막는다) 모델은 관여하지 않는다. 물놀이 제안에 "닭고기를 먹어봤나요" 가 뜨면
         *    그건 계약 위반이지 화면이 걸러 낼 일이 아니다 — 화면은 오는 대로 그린다.
         * ⚠️ 기준이 `agent === "food"` 인지 "재료가 있을 때" 인지는 서버 쪽 결정이다 (#151).
         */
        prechecks:
          suggestion.agent === "food"
            ? [{ code: "unknown_ingredient", item: "닭고기", note: "첫 기록" }]
            : [],
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
        suggestion_id?: string | null;
      };

      // 🚨 일자 없이 제출되면 안 된다. 화면이 막지만 계약도 막는다 (규칙은 코드가 진다).
      if (!body.event.starts_at) {
        return apiError(400, "invalid_request", "일자가 없는 일정은 만들 수 없어요");
      }

      const sid = body.suggestion_id ?? null;
      // 여기까지 왔다는 건 처음 보는 키라는 뜻이다 — 즉 새 요청이다.
      if (sid && submittedSuggestions.has(sid)) {
        return apiError(409, "already_confirmed", "이미 캘린더에 넣은 제안이에요");
      }
      if (sid) submittedSuggestions.add(sid);

      /**
       * 🚨 **보호자가 확인한 값을 그대로 돌려준다.** 제목이나 시각이 바뀌어 돌아오면
       *    "확인하고 넣은 것" 과 "들어간 것" 이 달라져 승인 게이트가 거짓말이 된다.
       * 🚨 `items` 는 **최종 목록**이다 — 배열에서 빠진 것은 저장되지 않는다 (#122).
       */
      const event = draftEvent({
        title: body.event.title,
        starts_at: body.event.starts_at,
        all_day: body.event.all_day,
        status: "confirmed",
        items: body.items.map((item, index) => ({
          item_id: item.item_id ?? `i_new_${index}`,
          item_name: item.item_name,
          is_prepared: false,
          prepared_at: null,
        })),
      });

      return HttpResponse.json({
        event,
        ...(sid ? { suggestion_status: "approved" as const } : {}),
      });
    }),
  ),
];
