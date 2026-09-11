import { http, HttpResponse } from "msw";

import { draftEvent, suggestions } from "../fixtures";
import { currentScenario } from "../scenario";
import { apiError, consentRequired, networkDelay, url } from "./helpers";
import { withIdempotency } from "./idempotency";

/**
 * 승인 게이트 ㉠ 을 이미 통과한 event.
 *
 * 🚨 여기 있다고 무조건 409 를 주면 안 된다. "확정은 됐는데 응답을 못 받아 같은 키로
 *    다시 보낸 재시도" 는 withIdempotency 가 먼저 가로채 처음 응답을 재생한다.
 *    이 409 는 **새 요청으로 이미 확정된 일정을 또 확정하려는 경우** 에만 나온다.
 */
const confirmedEvents = new Set<string>();

/** 테스트용. 목 서버는 프로세스 수명만큼 살아 있다. */
export function resetConfirmedEvents(): void {
  confirmedEvents.clear();
}

/** 05 제안 · 06 승인. */
export const suggestionHandlers = [
  http.post(url("/children/:cid/suggestions"), async () => {
    // Agent 호출이라 홈보다 느리다. 진행 오버레이가 실제로 보이는 시간.
    await networkDelay(900);
    const scenario = currentScenario();

    if (scenario === "consent") return consentRequired("child_health");

    // 🚨 근거가 없으면 개인화 대신 일반 추천이다. 되묻는 질문은 배열이 아니라 단수 — 한 개까지만.
    if (scenario === "scarcity" || scenario === "empty") {
      return HttpResponse.json({
        suggestions: [],
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
    if (scenario === "stale") {
      return HttpResponse.json({
        suggestions: [suggestions[1]],
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

  // 여기서는 아직 캘린더에 쓰지 않는다. draft 만 만들고 24시간 뒤 만료된다.
  http.post(url("/suggestions/:sid/event"), async ({ request }) => {
    await networkDelay();
    const body = (await request.json()) as { title?: string };
    const event = draftEvent(body.title ? { title: body.title } : {});
    return HttpResponse.json(
      {
        event,
        expires_at: event.starts_at,
        // 06 화면 상단 "확인해 주세요" 배너. 규칙이 만들고 모델은 관여하지 않는다.
        prechecks: [{ code: "unknown_ingredient", item: "닭고기", note: "첫 기록" }],
      },
      { status: 201 },
    );
  }),

  // 🚨 승인 게이트 ㉠ — 되돌릴 수 없는 지점.
  http.post(
    url("/events/:eid/confirm"),
    withIdempotency(async ({ params }) => {
      await networkDelay();
      const eventId = String(params.eid);

      // 여기까지 왔다는 건 처음 보는 키라는 뜻이다 — 즉 새 요청이다.
      if (confirmedEvents.has(eventId)) {
        return apiError(409, "already_confirmed", "이미 확정된 일정이에요");
      }
      confirmedEvents.add(eventId);

      return HttpResponse.json({
        event: draftEvent({ id: eventId, status: "confirmed" }),
        suggestion_status: "approved",
      });
    }),
  ),
];
