import { http, HttpResponse } from "msw";

import {
  affinities,
  CHILD_ID,
  daysAgo,
  emptyHome,
  healthSafety,
  home,
  newHealthSafety,
  observations,
  staleAffinities,
} from "../fixtures";
import { currentScenario } from "../scenario";
import { apiError, consentRequired, networkDelay, url } from "./helpers";
import { withIdempotency } from "./idempotency";

/** 새 요청으로 같은 항목을 또 넣는 것을 막는다 (키 재시도와는 다른 경로다). */
const registeredSafety = new Set(healthSafety.map((item) => `${item.type}:${item.label}`));

/** 테스트용. 목 서버는 프로세스 수명만큼 살아 있다. */
export function resetRegisteredSafety(): void {
  registeredSafety.clear();
  for (const item of healthSafety) registeredSafety.add(`${item.type}:${item.label}`);
}

/** 01·02 첫 진입 · 온보딩, 03 홈. */
export const childrenHandlers = [
  http.post(url("/children"), async ({ request }) => {
    await networkDelay();
    const body = (await request.json()) as { nickname: string };
    return HttpResponse.json(
      { id: CHILD_ID, nickname: body.nickname, age_display: "만 4세", role: "owner" },
      { status: 201 },
    );
  }),

  // 발달 검사가 아니다 — 보호자가 고른 값만 저장하고 AI 는 평가하지 않는다.
  http.get(url("/dev-screening/items"), async () => {
    await networkDelay();
    return HttpResponse.json({
      age_band: "만 4세 · 유아",
      items: [
        {
          item_id: "d4a",
          text: "가위로 선을 따라 종이를 자른다.",
          levels: [
            { level: 1, label: "아직" },
            { level: 2, label: "가끔" },
            { level: 3, label: "자주" },
          ],
        },
        {
          item_id: "d4b",
          text: "친구와 번갈아 가며 논다.",
          levels: [
            { level: 1, label: "아직" },
            { level: 2, label: "가끔" },
            { level: 3, label: "자주" },
          ],
        },
      ],
    });
  }),

  // 전부 선택이다. 모두 건너뛰어도 200 이다.
  http.post(
    url("/children/:cid/onboarding"),
    withIdempotency(async () => {
      await networkDelay(400);
      if (currentScenario() === "consent") return consentRequired("child_health");
      return HttpResponse.json({
        observations: observations.slice(0, 1),
        affinities: affinities.slice(1),
        safety: healthSafety,
        skipped: ["dev_answers"],
        run_id: "r01",
      });
    }),
  ),

  /**
   * 🚨 승인 게이트 ㉡ — 알레르기·건강 기록의 유일한 쓰기 경로 (NF-03).
   *
   * 확정 게이트와 같은 구조다. 같은 키 재시도는 withIdempotency 가 처음 응답을 재생하고,
   * 여기 409 는 **새 요청으로 같은 항목을 또 넣으려는 경우** 에만 나온다
   * (계약서: UNIQUE(child_id, type, label) — 같은 항목 재등록은 409).
   */
  http.post(
    url("/children/:cid/health-safety"),
    withIdempotency(async ({ request }) => {
      await networkDelay();
      if (currentScenario() === "consent") return consentRequired("child_health");

      const body = (await request.json()) as { type: string; label: string; category?: string };
      const key = `${body.type}:${body.label}`;
      if (registeredSafety.has(key)) {
        return apiError(409, "already_exists", "이미 등록된 항목이에요");
      }
      registeredSafety.add(key);

      return HttpResponse.json({ safety: newHealthSafety(body) }, { status: 201 });
    }),
  ),

  /**
   * 09 사진. 화면은 다음 이슈지만 되돌릴 수 없는 5개에 들어 있어서 여기 둔다 —
   * 표(lib/api/idempotency.ts)에 있는 경로는 목에도 있어야 계약 회귀 테스트가 성립한다.
   */
  http.post(
    url("/children/:cid/photos"),
    withIdempotency(async () => {
      await networkDelay(400);
      return HttpResponse.json({ run_id: `pr_${Date.now()}` }, { status: 202 });
    }),
  ),

  http.get(url("/children/:cid/home"), async () => {
    await networkDelay();
    const scenario = currentScenario();
    if (scenario === "consent") return consentRequired("child_health");
    if (scenario === "empty") return HttpResponse.json(emptyHome);
    if (scenario === "stale") {
      // 기억은 있는데 전부 낡았다. highlight 는 뜨지만 근거로는 못 쓴다.
      return HttpResponse.json({
        ...home,
        week_count: 0,
        highlight: {
          text: "계란 반찬을 찾은 기록이 있어요.",
          state_reason: staleAffinities[0].state_reason,
          ref: { kind: "profile_affinity" as const, id: staleAffinities[0].id },
        },
      });
    }
    return HttpResponse.json(home);
  }),

  http.get(url("/children/:cid/affinities"), async () => {
    await networkDelay();
    const scenario = currentScenario();
    if (scenario === "empty") return HttpResponse.json({ items: [], next_cursor: null });
    return HttpResponse.json({
      items: scenario === "stale" ? staleAffinities : affinities,
      next_cursor: null,
    });
  }),

  http.get(url("/children/:cid/observations"), async () => {
    await networkDelay();
    if (currentScenario() === "empty") {
      return HttpResponse.json({ items: [], next_cursor: null });
    }
    return HttpResponse.json({ items: observations, next_cursor: null });
  }),

  http.get(url("/children/:cid/health-safety"), async () => {
    await networkDelay();
    return HttpResponse.json({ items: healthSafety, updated_at: daysAgo(3) });
  }),
];
