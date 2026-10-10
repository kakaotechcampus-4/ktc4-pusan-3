import { describe, expect, it } from "vitest";

import { API_BASE_URL } from "@/lib/env";
import { ApiError, isApiError } from "@/lib/api/errors";
import { idempotentPath, newIdempotencyKey } from "@/lib/api/idempotency";
import {
  addHealthSafety,
  approveSuggestions,
  commitPhotoRun,
  createEventDrafts,
  submitEventDraft,
  photoFormData,
  submitOnboarding,
  uploadPhoto,
} from "@/lib/api/operations";
import {
  isDraftEvent,
  streamRunEvents,
  type EventDraftsEvent,
  type GuidanceEvent,
  type LaneEvent,
  type NoteEvent,
  type OfferEvent,
  type ParsedEvent,
  type RunEvent,
  type UnavailableEvent,
} from "@/lib/api/sse";
import { toISODate } from "@/lib/format";
import { api } from "@/lib/api/client";
import type {
  Affinity,
  AffinitiesResponse,
  Agent,
  AuthSession,
  ChildParentsResponse,
  ConsentResponse,
  ConsentsResponse,
  InviteAcceptResponse,
  InvitePreviewResponse,
  InviteResponse,
  Me,
  WithdrawResponse,
  CalendarDayResponse,
  CalendarMonthResponse,
  ChildProfile,
  CorrectionRequest,
  CorrectionResponse,
  GrowthLog,
  GrowthLogsResponse,
  HealthSafety,
  HealthSafetyListResponse,
  Observation,
  ObservationDetailResponse,
  ObservationsResponse,
  Policy,
  PhotoCommitResponse,
  PhotoEntry,
  PhotoLane,
  SafetyScanResponse,
  EventDraft,
  SubmitEventBody,
  SuggestionsRequest,
  SuggestionsResponse,
  SuggestionFeedbackResponse,
} from "@/lib/api/types";

import { INVITE_CODE_LENGTH, normalizeInviteCode } from "@/lib/invite-code";

import { submittedInput } from "./handlers/runs";

import { suggestionAllergens, suggestions } from "./fixtures";
import { setScenario } from "./scenario";

/**
 * 목이 **계약대로 행동하는가**.
 *
 * 멘토 리뷰(#29 질문 4): "필드 모양이 맞는 것과 API의 동작이 계약에 맞는 것은 별개예요.
 * 타입만으로는 동의 거부, 중복 처리, 상태 전이, SSE 이벤트 순서까지 확인할 수 없고요."
 *
 * 그래서 타입 검사(시드가 lib/api/types.ts 를 만족하는가)와 **별개로** 여기서 동작을 건다.
 * 이 파일이 통과한다는 건 화면이 개발 중에 보는 그 목이 아래를 지킨다는 뜻이다.
 */

/**
 * 초안 제출 본문. 🚨 **일자가 있어야 목이 받는다** — 화면이 잠그는 것과 같은 규칙을 계약도 건다.
 * 제안 id 를 넘기면 그 제안이 일정에 연결된다 (중복 제출 판정 대상 · #206).
 * 🚨 화면과 **같은 키**(`suggestion_ids` 배열)로 보낸다 — 한동안 단수 키로 보내서, 화면이 보내는
 *    배열을 목이 못 읽는 것을 이 테스트가 덮고 있었다.
 */
function draftBody(...suggestionIds: string[]): SubmitEventBody {
  return {
    event: {
      title: "지어낸 일정",
      starts_at: "2026-09-18T10:00:00+09:00",
      ends_at: null,
      all_day: false,
      event_type: "episodic",
      category: "activity",
    },
    items: [],
    ...(suggestionIds.length > 0 ? { suggestion_ids: suggestionIds } : {}),
  };
}

/**
 * 제안을 **채택해 두고** 그 제안에 연결되는 제출 본문을 만든다.
 * 🚨 실서버처럼 목도 채택 안 된 제안은 422, 없는 제안은 404 로 막는다 (#241) — 지어낸 id 를 넣으면
 *    재시도 · 중복 판정까지 가기 전에 거기서 떨어진다.
 */
async function approvedDraftBody(...suggestionIds: string[]): Promise<SubmitEventBody> {
  if (suggestionIds.length > 0) {
    await approveSuggestions("c1", { suggestion_ids: suggestionIds });
  }
  return draftBody(...suggestionIds);
}

/** 목이 계약서 경로를 그대로 쓰는지 확인하려면 URL 을 직접 만들어야 할 때가 있다. */
function raw(path: string, init?: RequestInit): Promise<Response> {
  return fetch(`${API_BASE_URL}${path}`, { method: "POST", ...init });
}

/**
 * 지금 유효한 약관 버전 (#90).
 *
 * 🚨 **테스트도 상수를 쓰지 않는다.** 화면과 같은 순서로 — 받아서, 그 값을 그대로 실어 —
 *    보내야 "프론트가 버전을 상수로 들고 있다" 는 회귀를 여기서도 잡는다. 목은 실서버와
 *    같은 검사를 걸고 있어서 (`handlers/policies.ts`), 상수를 쓰면 400 으로 떨어진다.
 */
async function policyVersion(scope: string): Promise<string> {
  const policy = (await api.get<Policy[]>("/policies")).find((p) => p.scope === scope);
  if (!policy) throw new Error(`GET /policies 에 ${scope} 가 없다`);
  return policy.version;
}

/** 화면이 보내는 모양 그대로의 동의 목록. */
async function agreed(scopes: readonly string[]) {
  const policies = await api.get<Policy[]>("/policies");
  return scopes.map((scope) => {
    const policy = policies.find((p) => p.scope === scope);
    if (!policy) throw new Error(`GET /policies 에 ${scope} 가 없다`);
    return { scope, policy_version: policy.version };
  });
}

describe("① 키 누락 — 표에 있는 5개 전부", () => {
  // 🚨 표(lib/api/idempotency.ts)에 줄을 더하고 목을 안 고치면 여기서 깨진다.
  const cases = Object.entries(idempotentPath).map(([name, build]) => [name, build("c1")] as const);

  it.each(cases)("%s 는 키가 없으면 400 이다", async (_name, path) => {
    const response = await raw(path, {
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    expect(response.status).toBe(400);
    const payload = (await response.json()) as { error: { code: string } };
    expect(payload.error.code).toBe("idempotency_key_required");
  });
});

describe("② 같은 키 · 같은 요청 = 재시도", () => {
  it("처음 응답을 그대로 돌려주고, 처리는 한 번만 한다", async () => {
    const key = newIdempotencyKey();

    const body = await approvedDraftBody("s_4");
    const first = await submitEventDraft("c1", body, key);
    const second = await submitEventDraft("c1", body, key);

    expect(first.event.id).toBeTruthy();
    // 두 번째가 409 로 오면 "성공했는데 응답을 못 받은" 경우가 실패처럼 보인다 — 그걸 막는 줄이다.
    expect(second).toEqual(first);
  });
});

describe("③ 같은 키 · 다른 요청 = 재사용 거부", () => {
  it("422 idempotency_key_reuse", async () => {
    const key = newIdempotencyKey();
    await submitOnboarding("c1", { gender: "female" }, key);

    await expect(submitOnboarding("c1", { gender: "male" }, key)).rejects.toSatisfy(
      (e: unknown) => isApiError(e, "idempotency_key_reuse") && e.status === 422,
    );
  });

  it("다른 엔드포인트에 같은 키를 돌려써도 거부한다", async () => {
    const key = newIdempotencyKey();
    await submitEventDraft("c1", draftBody(), key);

    await expect(
      addHealthSafety("c1", { type: "allergy", label: "지어낸항목", category: "식품" }, key),
    ).rejects.toSatisfy((e: unknown) => isApiError(e, "idempotency_key_reuse"));
  });
});

describe("④ 같은 키 · 동시 요청", () => {
  it("한 번만 실행되고 나머지는 409 idempotency_in_progress", async () => {
    const key = newIdempotencyKey();

    const body = await approvedDraftBody("s_4");
    const results = await Promise.allSettled([
      submitEventDraft("c1", body, key),
      submitEventDraft("c1", body, key),
      submitEventDraft("c1", body, key),
    ]);

    const fulfilled = results.filter((r) => r.status === "fulfilled");
    const rejected = results.filter((r) => r.status === "rejected");

    expect(fulfilled).toHaveLength(1);
    for (const r of rejected) {
      expect(isApiError(r.reason, "idempotency_in_progress")).toBe(true);
      expect((r.reason as ApiError).status).toBe(409);
    }
  });
});

describe("⑤ 재시도와 '이미 확정' 을 구분한다", () => {
  it("새 키로 이미 넣은 제안을 또 넣으면 409 already_confirmed", async () => {
    const body = await approvedDraftBody("s_4");
    await submitEventDraft("c1", body, newIdempotencyKey());

    // 같은 제안, 새 사용자 동작(= 새 키). 이건 재시도가 아니라 중복 제출이다.
    await expect(submitEventDraft("c1", body, newIdempotencyKey())).rejects.toSatisfy(
      (e: unknown) => isApiError(e, "already_confirmed") && e.status === 409,
    );
  });

  it("묶인 초안은 제안 하나만 겹쳐도 409 already_confirmed", async () => {
    await approveSuggestions("c1", { suggestion_ids: ["s_1", "s_2", "s_3"] });
    await submitEventDraft("c1", draftBody("s_1", "s_2"), newIdempotencyKey());

    // 🚨 일부만 받아 주면 같은 제안이 일정 두 개에 걸린다.
    await expect(
      submitEventDraft("c1", draftBody("s_2", "s_3"), newIdempotencyKey()),
    ).rejects.toSatisfy((e: unknown) => isApiError(e, "already_confirmed") && e.status === 409);
  });
});

/**
 * 초안 경로 — #121 · #122 에서 정한 것이 목에서도 지켜지는지.
 * 🚨 타입은 모양만 본다. "쓰지 않는다" 와 "food 일 때만 묻는다" 는 동작이라 여기서 건다.
 */
describe("일정 초안", () => {
  /** 🚨 **채택한 뒤에 만든다** — 화면 순서 그대로다. 안 하면 422 `not_approved` 다 (#241). */
  const makeDrafts = async (ids: string[]) => {
    await approveSuggestions("c1", { suggestion_ids: ids });
    return createEventDrafts("c1", { suggestion_ids: ids });
  };

  /**
   * 🚨 **제안 id 를 테스트에 박지 않는다.** 한 Agent 가 3가지씩 내게 되면서 픽스처 번호가 밀렸고,
   *    `s_2` 를 놀이로 박아 뒀던 테스트가 조용히 식사를 고르고 있었다 — 픽스처에서 뽑는다.
   */
  const firstOf = (agent: Agent) => {
    const found = suggestions.find((s) => s.agent === agent);
    if (!found) throw new Error(`픽스처에 ${agent} 제안이 없다`);
    return found.id;
  };

  it("초안을 만드는 호출은 저장된 event 가 아니라 초안을 준다 — 아직 행이 아니다", async () => {
    const created = await makeDrafts([firstOf("food")]);

    // 🚨 id · status · expires_at 이 없다. 있으면 DB 에 쓴 것이고, 그건 승인 게이트를 건너뛴 것이다.
    expect(created.drafts.length).toBeGreaterThan(0);
    for (const draft of created.drafts) {
      expect(draft.draft_id).toBeTruthy();
      expect(draft.op).toBe("create");
      expect(draft.event_id).toBeNull();
      expect(draft).not.toHaveProperty("id");
      expect(draft).not.toHaveProperty("status");
    }
  });

  it("🚨 같은 제안으로 다시 만들면 초안 id 가 달라진다 — 서버는 요청마다 새로 붙인다 (#241)", async () => {
    // 목이 제안에서 id 를 지어내면 화면이 같은 장으로 합치는 것을 우연히 통과한다.
    // 합치는 것은 `mergeDrafts` 의 일이다 (`stores/event-draft.test.ts`).
    const [first] = (await makeDrafts([firstOf("activity")])).drafts;
    const [again] = (await makeDrafts([firstOf("activity")])).drafts;

    expect(again.draft_id).not.toBe(first.draft_id);
    expect(again.suggestion_ids).toEqual(first.suggestion_ids);
  });

  it("🚨 채택하지 않은 제안은 초안이 되지 않는다 — 422 not_approved (#241)", async () => {
    await expect(
      createEventDrafts("c1", { suggestion_ids: [firstOf("activity")] }),
    ).rejects.toSatisfy((e: unknown) => isApiError(e, "not_approved") && e.status === 422);
  });

  it("🚨 없는 제안이 섞이면 걸러 내지 않고 404 not_found 다", async () => {
    const id = firstOf("activity");
    await approveSuggestions("c1", { suggestion_ids: [id] });

    await expect(
      createEventDrafts("c1", { suggestion_ids: [id, "s_does_not_exist"] }),
    ).rejects.toSatisfy((e: unknown) => isApiError(e, "not_found") && e.status === 404);
  });

  it("🚨 제안 초안은 일자가 비어 있다 — 서버가 오늘로 채우지 않는다", async () => {
    const created = await makeDrafts([firstOf("food")]);
    for (const draft of created.drafts) expect(draft.event.starts_at).toBeNull();
  });

  it("🚨 food 제안 여러 건은 한 끼로 묶인다 — 고른 수와 초안 수가 1:1 이 아니다", async () => {
    const foodIds = suggestions.filter((s) => s.agent === "food").map((s) => s.id);
    // 🚨 픽스처에 식사 제안이 하나뿐이면 묶기가 깨져도 이 테스트가 통과한다.
    expect(foodIds.length).toBeGreaterThan(1);

    const created = await makeDrafts(foodIds);
    expect(created.drafts).toHaveLength(1);
    // 🚨 묶인 초안은 **고른 제안 전부**를 달고 나간다. 단수로 두면 나머지가 draft 인 채 만료된다.
    expect(created.drafts[0].suggestion_ids).toEqual(foodIds);
  });

  it("🚨 food 가 아닌 제안은 고른 수만큼 초안이 나온다", async () => {
    const rest = suggestions.filter((s) => s.agent !== "food" && s.agent !== "health");
    const created = await makeDrafts(rest.map((s) => s.id));
    expect(created.drafts).toHaveLength(rest.length);
  });

  it("🚨 사전검사는 **고르는 응답**에 실리고, 어느 제안 것인지 말한다", async () => {
    const body: SuggestionsRequest = { agents: ["food", "activity"] };
    const data = await api.post<SuggestionsResponse>("/children/c1/suggestions", body);

    /**
     * 🚨 **채택할 때 물어야 해서 고르는 화면이 미리 들고 있어야 한다** (#151).
     *    초안을 만든 뒤에 물으면 **일정을 안 만드는 보호자에게는 영영 안 묻는다.**
     */
    const check = (data.prechecks ?? []).find((p) => p.code === "unknown_ingredient");
    expect(check).toBeDefined();

    /**
     * 🚨 `suggestion_id` 가 없으면 화면은 재료 하나로 **고른 것 전부**를 막는다
     *    (알레르기에서 덜 막는 쪽으로 기울 수 없다).
     */
    for (const p of data.prechecks ?? []) {
      expect(data.suggestions.some((s) => s.id === p.suggestion_id)).toBe(true);
      // 🚨 묻는 항목은 그 제안의 `allergens` 에서만 나온다 — 물놀이 제안에 재료 질문이 붙으면 안 된다.
      expect(suggestionAllergens[p.suggestion_id!]).toContain(p.item);
    }
  });

  /**
   * 🚨 **기준은 `agent` 가 아니라 제안의 `allergens` 다** (#233 · 문서 §3-8).
   *    `health_safety` 에 행이 없는 항목만 묻고, `active` 는 제안째 빼고, `retracted` 는 묻지 않는다.
   */
  describe("사전검사는 allergens 와 health_safety 로 정해진다", () => {
    const body: SuggestionsRequest = { agents: ["food", "activity"] };
    const fetchSuggestions = () => api.post<SuggestionsResponse>("/children/c1/suggestions", body);
    const asked = (data: SuggestionsResponse) =>
      (data.prechecks ?? []).map((p) => `${p.suggestion_id}:${p.item}`).sort();

    it("놀이 제안이어도 allergens 가 있으면 묻고, 없으면 food 여도 묻지 않는다", async () => {
      const data = await fetchSuggestions();
      const expected = data.suggestions
        .flatMap((s) => (suggestionAllergens[s.id] ?? []).map((a) => `${s.id}:${a}`))
        .sort();
      expect(asked(data)).toEqual(expected);

      // 🚨 픽스처가 두 경우를 다 갖고 있어야 이 테스트가 `agent` 기준으로 돌아가는 것을 잡는다.
      const withAllergens = (agent: Agent) =>
        data.suggestions.filter((s) => s.agent === agent && suggestionAllergens[s.id]?.length);
      expect(withAllergens("activity").length).toBeGreaterThan(0);
      expect(
        data.suggestions.some((s) => s.agent === "food" && !suggestionAllergens[s.id]?.length),
      ).toBe(true);
    });

    it("🚨 active 로 등록된 항목이 든 제안은 목록에서 빠지고 묻지도 않는다", async () => {
      const [id, [allergen]] = Object.entries(suggestionAllergens).find(([, a]) => a.length > 0)!;
      await addHealthSafety(
        "c1",
        { type: "allergy", label: allergen, category: "식품" },
        newIdempotencyKey(),
      );

      const data = await fetchSuggestions();
      expect(data.suggestions.map((s) => s.id)).not.toContain(id);
      expect(asked(data).some((a) => a.endsWith(`:${allergen}`))).toBe(false);
    });

    it("retracted 로 내린 항목은 다시 묻지 않고, 그 제안은 목록에 남는다", async () => {
      const [id, [allergen]] = Object.entries(suggestionAllergens).find(([, a]) => a.length > 0)!;
      const { safety } = await addHealthSafety(
        "c1",
        { type: "allergy", label: allergen, category: "식품" },
        newIdempotencyKey(),
      );
      await api.delete(`/children/c1/health-safety/${safety.id}`);

      const data = await fetchSuggestions();
      expect(data.suggestions.map((s) => s.id)).toContain(id);
      expect(asked(data)).not.toContain(`${id}:${allergen}`);
    });
  });

  it("🚨 초안 응답은 사전검사를 지지 않는다 — 물어보는 자리가 아니다", async () => {
    const created = await makeDrafts([firstOf("food")]);
    expect(created).not.toHaveProperty("prechecks");
  });

  it("🚨 채택은 캘린더와 다른 축이다 — status 만 바뀌고 일정은 안 생긴다", async () => {
    const body: SuggestionsRequest = { agents: ["food", "activity"] };
    const listed = await api.post<SuggestionsResponse>("/children/c1/suggestions", body);
    const picked = listed.suggestions.filter((s) => s.agent === "activity").slice(0, 2);

    const approved = await approveSuggestions("c1", {
      suggestion_ids: picked.map((s) => s.id),
    });

    /**
     * 🚨 **바뀐 행을 그대로 돌려준다.** 화면이 `status` 를 지어내면 서버가 일부만 채택했을 때
     *    화면과 서버가 갈린다.
     */
    expect(approved.suggestions.map((s) => s.id)).toEqual(picked.map((s) => s.id));
    for (const s of approved.suggestions) expect(s.status).toBe("approved");

    /**
     * 🚨 **채택했다고 일정이 생기지 않는다.** 이 축이 무너지면 "이걸로 할 건데 캘린더엔
     *    안 넣을래" 가 표현 불가능해지고, 캘린더 쓰기가 게이트 없이 일어난다 (최상위 §2).
     */
    const month = await api.get<CalendarMonthResponse>("/children/c1/calendar", {
      query: { month: monthOf(new Date()) },
    });
    const days = month.days.filter((d) => d.has_event).length;
    await approveSuggestions("c1", { suggestion_ids: picked.map((s) => s.id) });
    const after = await api.get<CalendarMonthResponse>("/children/c1/calendar", {
      query: { month: monthOf(new Date()) },
    });
    expect(after.days.filter((d) => d.has_event).length).toBe(days);
  });

  it("🚨 없는 제안을 채택하면 막는다 — 화면에 없는 것이 승인되지 않게", async () => {
    await expect(
      approveSuggestions("c1", { suggestion_ids: ["s_1", "s_does_not_exist"] }),
    ).rejects.toSatisfy((e: unknown) => isApiError(e, "not_found") && e.status === 404);
  });

  it("🚨 Agent 마다 후보가 여럿이고, 묶음 머리말이 함께 온다", async () => {
    const body: SuggestionsRequest = { agents: ["food", "activity"] };
    const data = await api.post<SuggestionsResponse>("/children/c1/suggestions", body);

    const byAgent = new Map<string, number>();
    for (const s of data.suggestions) byAgent.set(s.agent, (byAgent.get(s.agent) ?? 0) + 1);

    /**
     * 🚨 한 Agent 가 **3가지씩** 낸다 — 그 셋은 서로 다른 제안이 아니라 한 결정에 대한 대안이다.
     *    하나뿐이면 "3가지 중에서" 머리줄도 묶기도 화면에서 한 번도 돌지 않는다.
     */
    for (const [, count] of byAgent) expect(count).toBeGreaterThan(1);

    // 🚨 머리말은 후보를 담지 않는다 — 묶는 것은 화면이 `agent` 로 한다.
    for (const group of data.groups ?? []) {
      expect(byAgent.has(group.agent)).toBe(true);
      expect(group).not.toHaveProperty("suggestions");
    }
    // 🚨 `food` 만 묶인다. 이 값이 없으면 화면은 묶임을 **말하지 않는다**(지어내지 않는다).
    expect((data.groups ?? []).find((g) => g.agent === "food")?.merges_into_one).toBe(true);
    expect(
      (data.groups ?? []).find((g) => g.agent === "activity")?.merges_into_one,
    ).toBeUndefined();
  });

  it("🚨 개인화 추천의 근거마다 화면에 나갈 문구가 붙어 온다", async () => {
    const body: SuggestionsRequest = { agents: ["food", "activity"] };
    const data = await api.post<SuggestionsResponse>("/children/c1/suggestions", body);

    /**
     * 🚨 `suggestion_evidence.note` 는 **Agent 가 쓰고 보호자 화면에 그대로 나간다**
     *    (`docs/agents/data_model.md` · 2026-09-25). 비면 그 후보를 서버가 거절하므로
     *    화면에 도달할 수 없다 — 화면이 대신 문장을 만들면 **거절됐어야 할 후보를 UI 가 덮는다.**
     *    타입은 `note: string` 까지만 보고 **빈 문자열은 못 본다.** 그래서 여기서 건다.
     */
    for (const suggestion of data.suggestions) {
      expect(suggestion.evidence.length).toBeGreaterThan(0);
      for (const item of suggestion.evidence) expect(item.note.trim()).not.toBe("");
    }
  });

  it("🚨 일자 없이 제출하면 막는다 — 화면이 잠그는 것과 같은 규칙을 계약도 건다", async () => {
    const body = { ...draftBody(), event: { ...draftBody().event, starts_at: null } };

    await expect(
      submitEventDraft("c1", body as SubmitEventBody, newIdempotencyKey()),
    ).rejects.toSatisfy((e: unknown) => isApiError(e, "validation_failed") && e.status === 400);
  });

  it("🚨 채택하지 않은 제안을 제출하면 422 not_approved — 캘린더에 쓰지 않는다 (#241)", async () => {
    await expect(submitEventDraft("c1", draftBody("s_4"), newIdempotencyKey())).rejects.toSatisfy(
      (e: unknown) => isApiError(e, "not_approved") && e.status === 422,
    );
  });

  it("🚨 없는 제안을 제출하면 404 not_found", async () => {
    await expect(
      submitEventDraft("c1", draftBody("s_does_not_exist"), newIdempotencyKey()),
    ).rejects.toSatisfy((e: unknown) => isApiError(e, "not_found") && e.status === 404);
  });

  it("🚨 items 는 최종 목록이다 — 보낸 것만 저장된다", async () => {
    const body: SubmitEventBody = {
      ...(await approvedDraftBody("s_4")),
      items: [{ item_id: null, item_name: "수영복" }],
    };
    const saved = await submitEventDraft("c1", body, newIdempotencyKey());

    expect(saved.event.items.map((i) => i.item_name)).toEqual(["수영복"]);
    // 🚨 새 준비물의 체크는 언제나 false 다 — 초안이 정하는 것은 INSERT 초기값뿐이다.
    expect(saved.event.items.every((i) => !i.is_prepared)).toBe(true);
  });

  it("제출하면 확인한 값 그대로 저장된다 — 게이트가 거짓말하지 않는다", async () => {
    const body = await approvedDraftBody("s_4");
    const saved = await submitEventDraft("c1", body, newIdempotencyKey());

    expect(saved.event.title).toBe(body.event.title);
    expect(saved.event.starts_at).toBe(body.event.starts_at);
    // 🚨 `event.status` 는 없어진 필드다 (#118). 목이 계속 채우면 화면이 기대도 모르게 기댄다.
    expect(saved.event).not.toHaveProperty("status");
  });

  it("제출은 201 이고, 같은 키 재시도도 같은 201 을 재생한다 (#241)", async () => {
    const init = {
      headers: { "Content-Type": "application/json", "Idempotency-Key": newIdempotencyKey() },
      body: JSON.stringify(await approvedDraftBody("s_4")),
    };
    const first = await raw(idempotentPath.submitEvent("c1"), init);
    const replayed = await raw(idempotentPath.submitEvent("c1"), init);

    expect(first.status).toBe(201);
    expect(replayed.status).toBe(201);
  });

  it("🚨 제출은 제안 상태를 바꾸지 않는다 — approved 는 채택이 이미 만들었다 (#206)", async () => {
    const saved = await submitEventDraft("c1", await approvedDraftBody("s_4"), newIdempotencyKey());

    expect(saved).not.toHaveProperty("suggestion_status");
  });
});

describe("승인 게이트 ㉡", () => {
  it("승인 게이트 ㉡ 도 같은 구조다", async () => {
    const item = { type: "allergy", label: "지어낸알레르기", category: "식품" };

    const created = await addHealthSafety("c1", item, newIdempotencyKey());
    expect(created.safety.kind).toBe("health_safety");

    await expect(addHealthSafety("c1", item, newIdempotencyKey())).rejects.toSatisfy((e: unknown) =>
      isApiError(e, "already_exists"),
    );
  });
});

describe("⑥ 권한 부족 — 동의 거부", () => {
  it("403 consent_required 에 deeplink 가 붙고, 저장은 일어나지 않는다", async () => {
    setScenario("consent");
    try {
      const key = newIdempotencyKey();
      let caught: unknown;
      try {
        await submitOnboarding("c1", { gender: "female" }, key);
      } catch (e) {
        caught = e;
      }

      expect(isApiError(caught, "consent_required")).toBe(true);
      expect((caught as ApiError).status).toBe(403);
      expect((caught as ApiError).consentDeeplink).toBe("settings/consents");

      // 🚨 거부는 "처리 결과" 가 아니다. 동의를 받은 뒤 같은 키로 다시 보내면 통과해야 한다.
      setScenario("default");
      const retried = await submitOnboarding("c1", { gender: "female" }, key);
      expect(retried.run_id).toBe("r01");
    } finally {
      setScenario("default");
    }
  });
});

describe("⑦ 상태 전이 · SSE 순서", () => {
  it("saved 가 promoted 보다 먼저 온다", async () => {
    const { run_id } = await api.post<{ run_id: string }>(
      idempotentPath.input("c1"),
      { text: "지어낸 한 줄", source: "home_input" },
      { idempotencyKey: newIdempotencyKey() },
    );

    const seen: string[] = [];
    for await (const event of streamRunEvents(run_id)) seen.push(event.type);

    expect(seen).toContain("saved");
    expect(seen).toContain("promoted");
    expect(seen.indexOf("saved")).toBeLessThan(seen.indexOf("promoted"));
    expect(seen.at(-1)).toBe("done");
  });

  it("일정 초안은 한 프레임에 배열로 온다 — 저장 뒤 · done 전", async () => {
    const { run_id } = await api.post<{ run_id: string }>(
      idempotentPath.input("c1"),
      { text: "지어낸 한 줄", source: "home_input" },
      { idempotencyKey: newIdempotencyKey() },
    );

    const seen: string[] = [];
    let drafts: EventDraft[] = [];
    for await (const event of streamRunEvents(run_id)) {
      seen.push(event.type);
      if (isDraftEvent(event.type)) drafts = (event.data as EventDraftsEvent).drafts;
    }

    // 🚨 프레임이 하나다. 초안마다 한 장씩 오면 화면이 묶음을 한 번에 못 그린다.
    expect(seen.filter(isDraftEvent)).toHaveLength(1);
    expect(drafts.length).toBeGreaterThan(0);

    // 🚨 한 run 이 create 와 update 를 같이 낼 수 있다 — 화면이 op 로 엔드포인트를 가른다.
    expect(drafts.some((d) => d.op === "create")).toBe(true);
    const update = drafts.find((d) => d.op === "update");
    expect(update?.event_id).toBeTruthy();
    // 🚨 update 초안은 `before` 가 원본 전체다 — 없으면 "오후 3시 → 오후 5시" 를 못 그린다.
    expect(update?.before?.items).toBeDefined();

    // 🚨 초안은 저장된 게 아니다. id·status 가 붙으면 승인 게이트를 건너뛴 것이다.
    for (const d of drafts) {
      expect(d).not.toHaveProperty("id");
      expect(d).not.toHaveProperty("status");
      // 🚨 `is_prepared` 는 payload 에 없다 — 체크는 PATCH /event-items/{iid} 만의 몫이다 (#122).
      for (const item of d.items) expect(item).not.toHaveProperty("is_prepared");
    }
  });

  /**
   * 🚨 한 줄이 무엇을 만드는지는 발화에 달려 있다 (CLAUDE.md §5 의도 3형). 목이 늘 둘 다 내면
   *    "기록만 남는 한 줄" 과 "일정이 되는 한 줄" 의 화면을 따로 볼 수 없다.
   *    낱말 규칙은 목의 것이고 진짜 판정은 Supervisor 의 일이다 — 여기서 거는 것은
   *    **두 경로가 실제로 갈리는가**뿐이다.
   */
  it.each([
    ["기록형", "오늘 그림놀이 했대", { observations: true, drafts: false }],
    ["일정형", "이번 주말에 공원 산책 가기 저장해줘", { observations: false, drafts: true }],
    ["혼합형", "지어낸 한 줄", { observations: true, drafts: true }],
  ] as const)("%s 발화는 그에 맞는 것만 흘린다", async (_name, text, expected) => {
    const { run_id } = await api.post<{ run_id: string }>(
      idempotentPath.input("c1"),
      { text, source: "home_input" },
      { idempotencyKey: newIdempotencyKey() },
    );

    let observations = 0;
    let drafts = 0;
    for await (const event of streamRunEvents(run_id)) {
      if (event.type === "saved") {
        observations += (event.data as { observations: unknown[] }).observations.length;
      }
      if (isDraftEvent(event.type)) drafts += (event.data as EventDraftsEvent).drafts.length;
    }

    expect(observations > 0).toBe(expected.observations);
    expect(drafts > 0).toBe(expected.drafts);
  });

  it("failed 시나리오는 원문을 돌려주고 거기서 끝난다", async () => {
    setScenario("failed");
    try {
      const text = "지어낸 한 줄";
      const { run_id } = await api.post<{ run_id: string }>(
        idempotentPath.input("c1"),
        { text, source: "home_input" },
        { idempotencyKey: newIdempotencyKey() },
      );

      const events = [];
      for await (const event of streamRunEvents(run_id)) events.push(event);

      const last = events.at(-1);
      expect(last?.type).toBe("failed");
      expect((last?.data as { raw_text: string }).raw_text).toBe(text);
      // 저장된 게 없으니 saved 가 있으면 안 된다 (CLAUDE.md §4 — 다음 칸으로 전파하지 않는다).
      expect(events.map((e) => e.type)).not.toContain("saved");
    } finally {
      setScenario("default");
    }
  });
});

/* ── 04 안내 · 되묻기 · 준비 중 (#141) ───────────────────────────────── */

describe("⑳ 안내는 실패가 아니다", () => {
  /** 한 줄을 보내고 그 run 의 이벤트를 전부 모은다. */
  async function runOf(text = "지어낸 한 줄"): Promise<RunEvent[]> {
    const { run_id } = await api.post<{ run_id: string }>(
      idempotentPath.input("c1"),
      { text, source: "home_input" },
      { idempotencyKey: newIdempotencyKey() },
    );

    const events: RunEvent[] = [];
    for await (const event of streamRunEvents(run_id)) events.push(event);
    return events;
  }

  it("ping 이 와도 스트림은 그대로 done 까지 간다", async () => {
    const events = await runOf();
    // 🚨 `:` 주석이 아니라 이벤트여야 한다 — parseFrame 이 data 없는 프레임을 버려서
    //    주석으로는 20초 타이머가 안 되살아난다 (#140).
    expect(events.map((e) => e.type)).toContain("ping");
    expect(events.at(-1)?.type).toBe("done");
  });

  it("guidance 만 나간 run 은 저장 없이 done 으로 끝난다", async () => {
    setScenario("guidance");
    try {
      const events = await runOf();
      const types = events.map((e) => e.type);

      expect(types).toContain("guidance");
      // 🚨 실패로 끝나지 않는다. 안내는 run 을 멈추지 않는다 (#141).
      expect(types).not.toContain("failed");
      expect(events.at(-1)?.type).toBe("done");
      // 🚨 알레르기는 LLM 이 저장하지 않는다 (최상위 §2) — 그래서 saved 가 없는 것이 정상이다.
      expect(types).not.toContain("saved");

      const guidance = events.find((e) => e.type === "guidance")?.data as GuidanceEvent;
      expect(guidance.code).toBe("safety_record");
      expect(guidance.message.length).toBeGreaterThan(0);
    } finally {
      setScenario("default");
    }
  });

  it("🚨 섞인 한 줄은 안내와 저장이 같이 온다 — 안내가 저장을 감추면 안 된다", async () => {
    // "계란 잘 먹었어. 그리고 땅콩 알레르기 있어" — 앞은 저장되고 뒤는 안내로 돌아간다.
    setScenario("guidance_mixed");
    try {
      const events = await runOf();
      const types = events.map((e) => e.type);

      expect(types).toContain("guidance");
      expect(types).toContain("saved");
      // 안내는 라우팅이 내므로 Memory 의 저장보다 먼저다 (`pipeline.py`).
      expect(types.indexOf("guidance")).toBeLessThan(types.indexOf("saved"));
      expect(types).not.toContain("failed");
      expect(events.at(-1)?.type).toBe("done");

      // 🚨 같은 안내인데 두 시나리오가 다른 문구를 내면 화면이 두 경우를 다르게 그린다.
      const guidance = events.find((e) => e.type === "guidance")?.data as GuidanceEvent;
      expect(guidance.code).toBe("safety_record");
    } finally {
      setScenario("default");
    }
  });

  it("unavailable 은 저장보다 먼저 오고, 저장을 지우지 않는다", async () => {
    setScenario("unavailable");
    try {
      const events = await runOf();
      const types = events.map((e) => e.type);

      // 🚨 **이 순서가 요점이다.** 서버도 라우팅 직후 Memory 보다 먼저 보낸다 — 화면이 이걸
      //    실패로 받아 버리면 바로 뒤에 오는 저장이 화면에서 사라진다 (NF-06 · 최상위 §2).
      expect(types.indexOf("unavailable")).toBeLessThan(types.indexOf("saved"));
      expect(types).toContain("saved");
      expect(types).not.toContain("failed");
      expect(events.at(-1)?.type).toBe("done");

      const unavailable = events.find((e) => e.type === "unavailable")?.data as UnavailableEvent;
      expect(unavailable.agents).toEqual(["activity"]);

      // 🚨 준비 중이라고 말한 Agent 를 같은 화면에서 다시 권하지 않는다.
      const offer = events.find((e) => e.type === "offer")?.data as OfferEvent | undefined;
      expect(offer?.options.map((o) => o.agent)).not.toContain("activity");
    } finally {
      setScenario("default");
    }
  });

  it("🚨 일부만 저장되고 되묻기가 같이 오는 run 이 있다 — 답이 원문을 다시 보내면 안 되는 이유", async () => {
    // Memory 가 한 후보를 저장한 뒤 다른 후보의 정보가 모자라면 글로 되묻는다 (`pipeline.py`).
    // 이 대본이 없어서 #158 리뷰 전까지 "원문을 되돌려 이어 적기" 가 중복 저장을 만드는 걸 못 봤다.
    setScenario("note_mixed");
    try {
      const events = await runOf();
      const types = events.map((e) => e.type);

      expect(types).toContain("saved");
      expect(types).toContain("note");
      expect(types.indexOf("saved")).toBeLessThan(types.indexOf("note"));
      expect(events.at(-1)?.type).toBe("done");

      const note = events.find((e) => e.type === "note")?.data as NoteEvent;
      expect(note.kind).toBe("question");
    } finally {
      setScenario("default");
    }
  });

  it("답은 원문 대신 `reply_to` 로 이전 run 을 가리킨다", async () => {
    setScenario("note_question");
    try {
      const first = await askedRun();

      // 🚨 답에는 **앞 문장이 들어 있지 않다.** 넣으면 이미 저장된 조각이 다시 저장된다 (#158).
      const answer = "지어낸 답";
      const second = await answerTo(first, answer);

      const recorded = submittedInput(second);
      expect(recorded?.text).toBe(answer);
      expect(recorded?.replyTo).toBe(first);
      // 앞 run 은 답을 가리키지 않는다 — 참조는 한 방향이다.
      expect(submittedInput(first)?.replyTo).toBeUndefined();
    } finally {
      setScenario("default");
    }
  });

  it("되묻기만 있는 run 도 done 으로 끝난다 — 화면이 그릴 것은 질문 하나다", async () => {
    setScenario("note_question");
    try {
      const events = await runOf();
      const types = events.map((e) => e.type);

      expect(types).toContain("note");
      expect(types).not.toContain("saved");
      expect(events.at(-1)?.type).toBe("done");

      // 🚨 `kind` 가 없으면 화면은 질문으로 취급하지 않는다 (`sse.ts`) — 되묻기 동선이 통째로
      //    사라지므로, 목이 이 필드를 빠뜨리면 여기서 잡힌다.
      const note = events.find((e) => e.type === "note")?.data as NoteEvent;
      expect(note.kind).toBe("question");
      expect(note.text.length).toBeGreaterThan(0);
    } finally {
      setScenario("default");
    }
  });
});

/** 되묻는 run 을 하나 끝까지 받는다. 🚨 스트림까지 받아야 목이 그 질문을 맡아 둔다 (서버와 같다). */
async function askedRun(childId = "c1"): Promise<string> {
  const { run_id } = await api.post<{ run_id: string }>(
    idempotentPath.input(childId),
    { text: "지어낸 한 줄", source: "home_input" },
    { idempotencyKey: newIdempotencyKey() },
  );
  for await (const event of streamRunEvents(run_id)) void event;
  return run_id;
}

/** 그 run 의 질문에 답한다. 202 가 아니면 던진다. */
async function answerTo(
  replyTo: string,
  text = "지어낸 답",
  { childId = "c1", key = newIdempotencyKey() } = {},
): Promise<string> {
  const { run_id } = await api.post<{ run_id: string }>(
    idempotentPath.input(childId),
    { text, source: "home_input", reply_to: replyTo },
    { idempotencyKey: key },
  );
  return run_id;
}

async function eventsOf(runId: string): Promise<RunEvent[]> {
  const events: RunEvent[] = [];
  for await (const event of streamRunEvents(runId)) events.push(event);
  return events;
}

function failureOf(promise: Promise<unknown>): Promise<unknown> {
  return promise.then(() => null).catch((e: unknown) => e);
}

describe("㉒ 되묻기 답은 그 질문에 한 번만 이어진다 (#175)", () => {
  it("되물은 적 없는 run 에 답하면 400 reply_context_unavailable 이다", async () => {
    // default 대본은 되묻지 않는다 — 가리킬 질문이 없다.
    const plain = await askedRun();
    const failure = await failureOf(answerTo(plain));

    expect(isApiError(failure, "reply_context_unavailable")).toBe(true);
    expect((failure as ApiError).status).toBe(400);
    // 🚨 서버 문구와 같은 글자다 (`routers/children.py`). 상황만 말하고, 무엇을 다시 보낼지는
    //    화면이 이 아래에 적는다 — "다시 적어 주세요" 가 섞이면 화면의 "다시 적지 않아도 돼요" 와 부딪친다 (#208).
    expect((failure as ApiError).message).toBe("이전 질문을 이어서 확인할 수 없어요.");
  });

  it("같은 질문에 두 번 답하면 두 번째는 400 이다 — 관찰이 두 행이 되지 않게", async () => {
    setScenario("note_question");
    try {
      const asked = await askedRun();
      await answerTo(asked);

      // 🚨 새 키다 — 같은 키면 재생이라 400 이 아니라 처음 run 이 돌아온다 (아래 재시도 테스트).
      const failure = await failureOf(answerTo(asked, "또 다른 답"));
      expect(isApiError(failure, "reply_context_unavailable")).toBe(true);
    } finally {
      setScenario("default");
    }
  });

  it("맥락을 놓친 400 은 같은 키로 다시 보내도 계속 400 이다 — 다시 시도로 풀리지 않는다", async () => {
    setScenario("reply_unavailable");
    try {
      const asked = await askedRun();
      const key = newIdempotencyKey();

      for (const attempt of [1, 2]) {
        const failure = await failureOf(answerTo(asked, "지어낸 답", { key }));
        expect(isApiError(failure, "reply_context_unavailable"), `${attempt}번째 시도`).toBe(true);
      }

      // 🚨 질문을 놓고 새 입력으로 보내면 받는다 — 화면이 가는 길이 이것이다.
      const { run_id } = await api.post<{ run_id: string }>(
        idempotentPath.input("c1"),
        { text: "지어낸 한 줄과 답", source: "home_input" },
        { idempotencyKey: newIdempotencyKey() },
      );
      expect(submittedInput(run_id)?.replyTo).toBeUndefined();
    } finally {
      setScenario("default");
    }
  });

  it("다른 아이의 질문에는 답할 수 없다", async () => {
    setScenario("note_question");
    try {
      const asked = await askedRun("c1");
      const failure = await failureOf(answerTo(asked, "지어낸 답", { childId: "c2" }));
      expect(isApiError(failure, "reply_context_unavailable")).toBe(true);
    } finally {
      setScenario("default");
    }
  });

  it("🚨 이어받기 run 이 실패하면 같은 키 · 같은 `reply_to` 로 다시 보내 저장할 수 있다", async () => {
    // 화면의 "다시 시도" 가 `reply_to` 를 잃으면 안 되는 이유다. 서버는 실패한 run 의 맥락을
    // 원래 자리에 되돌려 두고 키도 놓아준다 (`runner.py`) — 같은 요청이 새 run 으로 다시 돈다.
    setScenario("reply_failed");
    try {
      const asked = await askedRun();
      const key = newIdempotencyKey();

      const firstTry = await answerTo(asked, "지어낸 답", { key });
      expect((await eventsOf(firstTry)).at(-1)?.type).toBe("failed");

      const retry = await answerTo(asked, "지어낸 답", { key });
      expect(retry).not.toBe(firstTry);
      expect(submittedInput(retry)?.replyTo).toBe(asked);

      const events = await eventsOf(retry);
      const types = events.map((e) => e.type);
      expect(types).toContain("saved");
      // 🚨 이어받은 답은 또 묻지 않는다 — `reply_to` 가 빠졌다면 새 입력이라 질문이 다시 떴다.
      //    note 자체는 온다: 서버가 이어받기에서 조기 종료를 꺼서 저장 뒤에 한 번 더 말한다 (#208).
      //    그 말이 **질문이 아니어야** 한다 — 질문이면 화면이 끝난 답에 답할 자리를 또 연다.
      const notes = events.filter((e) => e.type === "note").map((e) => e.data as NoteEvent);
      expect(notes.length).toBeGreaterThan(0);
      expect(notes.every((note) => note.kind !== "question")).toBe(true);
      expect(types.indexOf("saved")).toBeLessThan(types.indexOf("note"));
      expect(types.at(-1)).toBe("done");
    } finally {
      setScenario("default");
    }
  });
});

describe("㉑ 하루 한도는 다시 시도로 풀리지 않는다", () => {
  it("같은 키로 다시 보내도 계속 429 다", async () => {
    setScenario("daily_limit");
    try {
      const key = newIdempotencyKey();
      const body = { text: "지어낸 한 줄", source: "home_input" as const };

      for (const attempt of [1, 2]) {
        // 🚨 4xx 는 래퍼가 저장하지 않으므로(`isReplayable`) 핸들러가 다시 돌고 또 429 다 —
        //    화면에 "다시 시도" 버튼을 세우면 누를 때마다 같은 실패를 받는다 (#147).
        const failure = await api
          .post(idempotentPath.input("c1"), body, { idempotencyKey: key })
          .then(() => null)
          .catch((e: unknown) => e);

        expect(isApiError(failure, "daily_input_limit"), `${attempt}번째 시도`).toBe(true);
        expect((failure as ApiError).status).toBe(429);
      }
    } finally {
      setScenario("default");
    }
  });
});

/* ── 07 기억 · 교정 ──────────────────────────────────────────────────── */

describe("⑧ 관찰과 프로필은 다른 엔드포인트다", () => {
  it("도메인을 생략하면 건강을 뺀 4개 테이블을 병합해 observed_to DESC 로 내려준다", async () => {
    const page = await api.get<ObservationsResponse>("/children/c1/observations");

    const kinds = new Set(page.items.map((item) => item.kind));
    expect(kinds.size).toBeGreaterThan(1);
    // 🚨 건강 관찰은 첫 배포 범위 밖이다 — 서버(#266)가 목록에서 뺀다 (#259).
    expect(kinds).not.toContain("observation_health");
    expect(page.total).toBe(page.items.length);

    const dates = page.items.map((item) => item.observed_to);
    expect([...dates].sort().reverse()).toEqual(dates);
  });

  it("도메인 필터는 그 테이블만 내려준다", async () => {
    const page = await api.get<ObservationsResponse>("/children/c1/observations", {
      query: { domain: "food" },
    });

    expect(page.items.length).toBeGreaterThan(0);
    expect(page.items.every((item) => item.kind === "observation_food")).toBe(true);
  });

  // 🚨 `observation_${domain}` 으로 찾으면 없는 테이블(`observation_growth`)을 찾아 늘 비었다 (#269).
  it("growth 는 education 과 routine 두 테이블을 같이 내려준다", async () => {
    const page = await api.get<ObservationsResponse>("/children/c1/observations", {
      query: { domain: "growth" },
    });

    expect(new Set(page.items.map((item) => item.kind))).toEqual(
      new Set(["observation_education", "observation_routine"]),
    );
  });

  it("health 는 오류가 아니라 빈 목록이다", async () => {
    const page = await api.get<ObservationsResponse>("/children/c1/observations", {
      query: { domain: "health" },
    });

    expect(page).toEqual({ items: [], next_cursor: null, total: 0 });
  });

  it("next_cursor 를 그대로 돌려주면 빠지거나 겹치는 기록 없이 끝까지 넘어간다", async () => {
    const whole = await api.get<ObservationsResponse>("/children/c1/observations");

    const seen: string[] = [];
    let cursor: string | null = null;
    do {
      const page: ObservationsResponse = await api.get<ObservationsResponse>(
        "/children/c1/observations",
        { query: { limit: 2, cursor } },
      );
      expect(page.total).toBe(whole.total);
      seen.push(...page.items.map((item) => `${item.kind}:${item.id}`));
      cursor = page.next_cursor;
    } while (cursor !== null);

    expect(seen).toEqual(whole.items.map((item) => `${item.kind}:${item.id}`));
  });

  it("기본 한 장은 20건이고, 넘치면 next_cursor 가 온다", async () => {
    setScenario("observations_many");
    try {
      const first = await api.get<ObservationsResponse>("/children/c1/observations");
      expect(first.items).toHaveLength(20);
      expect(first.total).toBe(25);
      expect(first.next_cursor).not.toBeNull();

      const second = await api.get<ObservationsResponse>("/children/c1/observations", {
        query: { cursor: first.next_cursor },
      });
      expect(second.items).toHaveLength(5);
      expect(second.next_cursor).toBeNull();
    } finally {
      setScenario("default");
    }
  });

  it("깨진 커서는 400 validation_failed 다", async () => {
    const failure = await api
      .get<ObservationsResponse>("/children/c1/observations", { query: { cursor: "깨진값" } })
      .catch((error: unknown) => error);

    expect(isApiError(failure, "validation_failed")).toBe(true);
    expect((failure as ApiError).status).toBe(400);
  });

  it("프로필은 affinities 한 배열이고 safety 가 따로 온다", async () => {
    const body = await api.get<AffinitiesResponse>("/children/c1/affinities");

    expect(Array.isArray(body.affinities)).toBe(true);
    // 🚨 기억이 비어도 안전 정보는 비지 않는다 — 감쇠가 없다 (계약서 §07).
    expect(body.safety.length).toBeGreaterThan(0);
  });

  // 목록에서는 빠졌지만 캘린더와 run 결과에는 아직 선다 — 그 화면들이 같은 시트를 연다.
  it("health 관찰에는 subject · polarity · affinity 키가 아예 없다", async () => {
    const { observation: health } = await api.get<ObservationDetailResponse>(
      "/children/c1/observations/observation_health/o_h1",
    );

    expect(health.kind).toBe("observation_health");
    expect("subject" in health).toBe(false);
    expect("polarity" in health).toBe(false);
    expect("affinity" in health).toBe(false);
  });
});

describe("⑨ 교정은 지우지 않고 내린다", () => {
  it("target_ref 를 배열로 보내면 400 이다 — 서버는 본문 검증 실패를 400 으로 준다", async () => {
    const response = await fetch(`${API_BASE_URL}/corrections`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        target_ref: [{ kind: "profile_affinity", id: "a_12" }],
        verdict: "wrong",
        child_id: "c1",
      }),
    });

    expect(response.status).toBe(400);
  });

  it("once_only 는 status 를 stand_alone 으로 바꾼다 — active 로 두지 않는다 (#277)", async () => {
    const before = await api.get<ObservationsResponse>("/children/c1/observations");
    const target = before.items.find((item) => item.kind === "observation_food");
    expect(target).toBeDefined();

    const result = await api.post<CorrectionResponse>("/corrections", {
      target_ref: { kind: target!.kind, id: target!.id },
      verdict: "once_only",
      child_id: "c1",
    });

    expect((result.target as Observation).status).toBe("stand_alone");
  });

  it("wrong 은 행을 지우지 않고 status 를 inactive 로 내린다", async () => {
    const before = await api.get<ObservationsResponse>("/children/c1/observations");
    const target = before.items.find((item) => item.kind === "observation_education");
    expect(target).toBeDefined();

    const result = await api.post<CorrectionResponse>("/corrections", {
      target_ref: { kind: target!.kind, id: target!.id },
      verdict: "wrong",
      child_id: "c1",
    });

    // 🚨 `target` 은 관찰이거나 프로필이다 — 어느 쪽인지는 `target_ref.kind` 가 정한다.
    expect((result.target as Observation).status).toBe("inactive");
    // 🚨 고친 기록도 목록에 남는다 — 서버는 deleted 만 뺀다 (#266). 화면이 status 로 갈라 그린다.
    const after = await api.get<ObservationsResponse>("/children/c1/observations");
    expect(after.items.find((item) => item.id === target!.id)?.status).toBe("inactive");
    expect(after.total).toBe(before.total);
  });

  it("이번만 그랬어요로 고친 기록도 목록에 stand_alone 으로 남고, 상세에 이력이 선다", async () => {
    const result = await api.post<CorrectionResponse>("/corrections", {
      target_ref: { kind: "observation_food", id: "o_1" },
      verdict: "once_only",
      child_id: "c1",
    });

    const list = await api.get<ObservationsResponse>("/children/c1/observations");
    expect(list.items.find((item) => item.id === "o_1")?.status).toBe("stand_alone");

    const detail = await api.get<ObservationDetailResponse>(
      "/children/c1/observations/observation_food/o_1",
    );
    expect(detail.observation.status).toBe("stand_alone");
    expect(detail.corrections.map((c) => c.id)).toEqual([result.correction.id]);
  });

  it("이미 고친 기록을 또 고치면 409 already_corrected 다 — 값이 틀린 400 과 다르다 (#277)", async () => {
    const body = {
      target_ref: { kind: "observation_food", id: "o_1" },
      verdict: "once_only",
      child_id: "c1",
    } as const;
    await api.post<CorrectionResponse>("/corrections", body);

    const failure = await api
      .post<CorrectionResponse>("/corrections", { ...body, verdict: "wrong" })
      .catch((error: unknown) => error);

    expect(isApiError(failure, "already_corrected")).toBe(true);
    expect((failure as ApiError).status).toBe(409);
  });

  it("status 를 고르면 그 상태만 오고 total 도 그 상태로 센다 (#266)", async () => {
    await api.post<CorrectionResponse>("/corrections", {
      target_ref: { kind: "observation_food", id: "o_1" },
      verdict: "once_only",
      child_id: "c1",
    });

    const all = await api.get<ObservationsResponse>("/children/c1/observations");
    const standAlone = await api.get<ObservationsResponse>("/children/c1/observations", {
      query: { status: "stand_alone" },
    });
    const active = await api.get<ObservationsResponse>("/children/c1/observations", {
      query: { status: "active" },
    });

    expect(standAlone.items.map((item) => item.id)).toEqual(["o_1"]);
    expect(standAlone.total).toBe(1);
    expect(active.items.some((item) => item.id === "o_1")).toBe(false);
    expect(active.total).toBe(all.total - 1);
  });

  it("모르는 status 는 400 이다", async () => {
    const failure = await api
      .get<ObservationsResponse>("/children/c1/observations", { query: { status: "deleted" } })
      .catch((error: unknown) => error);

    expect(isApiError(failure, "validation_failed")).toBe(true);
    expect((failure as ApiError).status).toBe(400);
  });

  it("기록에 기억 판정(need_more_observation)을 보내면 400 이다", async () => {
    const failure = await api
      .post<CorrectionResponse>("/corrections", {
        target_ref: { kind: "observation_food", id: "o_1" },
        verdict: "need_more_observation",
        child_id: "c1",
      })
      .catch((error: unknown) => error);

    expect(isApiError(failure, "validation_failed")).toBe(true);
    expect((failure as ApiError).status).toBe(400);
  });

  // 🚨 기억 고치기는 의견이다 — 서버(#277)는 상태를 쌓인 기록 수와 기억 wrong 수로만 다시 센다.
  //    어느 판정도 기억을 archived 로 보내지 않고, 기억은 목록에 남는다.
  async function correctAffinity(id: string, verdict: CorrectionRequest["verdict"]) {
    return api.post<CorrectionResponse>("/corrections", {
      target_ref: { kind: "profile_affinity", id },
      verdict,
      child_id: "c1",
    });
  }

  async function listedAffinity(id: string): Promise<Affinity | undefined> {
    const body = await api.get<AffinitiesResponse>("/children/c1/affinities");
    return body.affinities.find((a) => a.id === id);
  }

  it("기억 need_more_observation 은 strength 만 낮춘다 — 확인됨은 확인됨으로 남는다", async () => {
    const before = await listedAffinity("a_12");
    const result = await correctAffinity("a_12", "need_more_observation");

    const target = result.target as Affinity;
    expect(target.state).toBe("confirmed");
    expect(target.strength).toBeLessThan(before!.strength);
    expect((await listedAffinity("a_12"))?.state).toBe("confirmed");
  });

  it("기억 outdated 도 상태를 바꾸지 않고 목록에 남긴다", async () => {
    const result = await correctAffinity("a_20", "outdated");

    expect((result.target as Affinity).state).toBe("candidate");
    expect(await listedAffinity("a_20")).toBeDefined();
  });

  it("기억 wrong 은 확인됨의 기준을 올린다 — 기록이 모자라면 후보로 내려가고 목록에 남는다", async () => {
    // a_12 는 묶인 기록 3건으로 확인됨이다. wrong 1번이면 기준이 4건이 된다.
    const result = await correctAffinity("a_12", "wrong");

    expect((result.target as Affinity).state).toBe("candidate");
    expect((await listedAffinity("a_12"))?.state).toBe("candidate");
  });

  it("묶인 기록을 고치면 기억도 다시 센다 — 고친 기록은 기억의 기록 수에서 빠진다", async () => {
    await api.post<CorrectionResponse>("/corrections", {
      target_ref: { kind: "observation_food", id: "o_1" },
      verdict: "once_only",
      child_id: "c1",
    });

    const affinity = await listedAffinity("a_12");
    expect(affinity?.observation_count).toBe(2);
    expect(affinity?.source_refs.some((ref) => ref.id === "o_1")).toBe(false);
    expect(affinity?.state).toBe("candidate");
  });

  it("기억에 once_only 를 보내면 400 이다 — 기록에만 쓰는 값", async () => {
    const failure = await correctAffinity("a_12", "once_only").catch((error: unknown) => error);

    expect(isApiError(failure, "validation_failed")).toBe(true);
    expect((failure as ApiError).status).toBe(400);
  });
});

describe("⑩ 피드백은 기억을 바꾸지 않는다", () => {
  it("memory_changed 가 항상 false 다", async () => {
    const body = await api.patch<SuggestionFeedbackResponse>("/suggestions/s_past_2/feedback", {
      feedback: "child_disliked",
    });

    expect(body.suggestion.feedback).toBe("child_disliked");
    // 🚨 화면 문구("기억은 그대로 둬요")와 같은 사실이다 (계약서 §08).
    expect(body.memory_changed).toBe(false);
  });

  it("피드백을 보내도 관찰 건수가 그대로다", async () => {
    const before = await api.get<ObservationsResponse>("/children/c1/observations");
    await api.patch("/suggestions/s_past_1/feedback", { feedback: "not_acted" });
    const after = await api.get<ObservationsResponse>("/children/c1/observations");

    expect(after.total).toBe(before.total);
  });
});

/* ── 09 캘린더 ───────────────────────────────────────────────────────── */

describe("⑪ 일기는 관찰이 아니다", () => {
  it("일기를 써도 관찰이 늘지 않는다", async () => {
    const date = (
      await api.get<CalendarMonthResponse>("/children/c1/calendar", {
        query: { month: monthOf(new Date()) },
      })
    ).days[0]?.date;
    expect(date).toBeDefined();

    const before = await api.get<ObservationsResponse>("/children/c1/observations");

    await api.put<CalendarDayResponse>(`/children/c1/calendar/${date}`, {
      text: "지어낸 일기 한 줄",
      image_urls: [],
      event_ids: [],
    });

    const after = await api.get<ObservationsResponse>("/children/c1/observations");
    // 🚨 계약서 §09 — 일기는 명시적으로 태우지 않는 한 관찰로 추출되지 않는다.
    expect(after.total).toBe(before.total);

    const day = await api.get<CalendarDayResponse>(`/children/c1/calendar/${date}`);
    expect(day.diary?.text).toBe("지어낸 일기 한 줄");
  });

  it("월 조회의 has_event 는 그날 일정이 실제로 있는 날만 켠다", async () => {
    // 🚨 **일정이 있는 달에서 건다.** 이번 달로 고정하면 일정이 다음 달에 있는 이틀 동안
    //    빈 목록을 훑고 아무것도 확인하지 않은 채 통과한다 (아래 `findEventDay` 주석).
    const eventDay = await findEventDay();
    const month = await api.get<CalendarMonthResponse>("/children/c1/calendar", {
      query: { month: eventDay.date.slice(0, 7) },
    });
    // 🚨 **"이번 달" 로 묻지 않는다.** 픽스처의 일정은 지금부터 +72시간이라 월말에는 다음
    //    달로 넘어간다 — 그러면 아래 반복문이 한 번도 안 돌아 아무것도 걸지 못한다
    //    (실제로 9월 29일에 이 파일이 깨졌다).
    expect(month.days.some((day) => day.has_event)).toBe(true);

    // 🚨 초안은 `event` 행이 아니라서(#118) 여기 셀 것이 없다 — 표식이 선 날은 하루 조회에도 일정이 있어야 한다.
    for (const day of month.days) {
      if (!day.has_event) continue;
      const detail = await api.get<CalendarDayResponse>(`/children/c1/calendar/${day.date}`);
      expect(detail.events.length).toBeGreaterThan(0);
    }
  });

  it("준비물 체크는 다시 읽어도 남아 있다", async () => {
    await api.patch("/event-items/i_1", { is_prepared: true });

    const eventDay = await findEventDay();
    const detail = await api.get<CalendarDayResponse>(`/children/c1/calendar/${eventDay.date}`);
    const item = detail.events.flatMap((event) => event.items).find((i) => i.item_id === "i_1");
    expect(item?.is_prepared).toBe(true);
  });
});

describe("⑧ 10 설정 — 동의 · 함께 보는 보호자", () => {
  /**
   * 🚨 `child_health` 는 **화면에 철회 버튼이 없는** 필수 동의다. 그래도 API 로는 철회가
   *    가능하고(계약서 §04 는 스코프를 가리지 않는다), append-only 는 모든 스코프에서
   *    지켜져야 한다 — 화면이 안 부른다고 목이 안 지켜도 되는 것이 아니다.
   */
  it("철회는 행을 지우는 게 아니라 withdrawn 행을 더한다 (append-only)", async () => {
    const before = await api.get<ConsentsResponse>("/consents", { query: { child_id: "c1" } });
    expect(before.effective.child_health).toBe(true);

    await api.post<ConsentResponse>("/consents", {
      scope: "child_health",
      action: "withdrawn",
      child_id: "c1",
      policy_version: await policyVersion("child_health"),
    });

    const after = await api.get<ConsentsResponse>("/consents", { query: { child_id: "c1" } });
    expect(after.effective.child_health).toBe(false);
    // 🚨 이력은 증빙이라 지워지지 않는다. 철회 한 줄이 **더해져** 있어야 한다.
    expect(after.history).toHaveLength(1);
    expect(after.history[0]).toMatchObject({ scope: "child_health", action: "withdrawn" });
  });

  it("껐다 켜면 이력 두 줄이 남고 현재 상태는 켜짐이다", async () => {
    for (const action of ["withdrawn", "granted"] as const) {
      await api.post<ConsentResponse>("/consents", {
        scope: "location",
        action,
        child_id: "c1",
        policy_version: await policyVersion("location"),
      });
    }

    const res = await api.get<ConsentsResponse>("/consents", { query: { child_id: "c1" } });
    expect(res.effective.location).toBe(true);
    expect(res.history.filter((h) => h.scope === "location")).toHaveLength(2);
  });

  it("POST /consents 의 effective 와 GET /consents 의 effective 가 같은 표를 본다", async () => {
    const posted = await api.post<ConsentResponse>("/consents", {
      scope: "location",
      action: "granted",
      child_id: "c1",
      policy_version: await policyVersion("location"),
    });
    const fetched = await api.get<ConsentsResponse>("/consents", { query: { child_id: "c1" } });
    expect(fetched.effective).toEqual(posted.effective);
  });

  it("owner 는 연결을 끊을 수 없다 — 409 owner_required", async () => {
    const before = await api.get<ChildParentsResponse>("/children/c1/parents");
    const owner = before.parents.find((p) => p.role === "owner");
    expect(owner).toBeDefined();

    const caught = await api.delete(`/children/c1/parents/${owner!.parent_id}`).catch((e) => e);
    expect(isApiError(caught, "owner_required")).toBe(true);
    expect((caught as ApiError).status).toBe(409);

    const after = await api.get<ChildParentsResponse>("/children/c1/parents");
    expect(after.parents).toHaveLength(before.parents.length);
  });

  it("member 는 끊을 수 있고 목록에서 빠진다", async () => {
    const before = await api.get<ChildParentsResponse>("/children/c1/parents");
    const member = before.parents.find((p) => p.role === "member");
    expect(member).toBeDefined();

    await api.delete(`/children/c1/parents/${member!.parent_id}`);

    const after = await api.get<ChildParentsResponse>("/children/c1/parents");
    expect(after.parents.map((p) => p.parent_id)).not.toContain(member!.parent_id);
  });

  it("초대 코드는 부를 때마다 새로 나온다 — 한 코드는 한 번만 쓴다", async () => {
    const first = await api.post<InviteResponse>("/children/c1/invites", {});
    const second = await api.post<InviteResponse>("/children/c1/invites", {});

    expect(first.invite_code).not.toBe(second.invite_code);
    expect(new Date(first.expires_at).getTime()).toBeGreaterThan(Date.now());
  });

  /**
   * 🚨 아이와 어떤 사이인지는 **받는 쪽이 고르는 값**이라 화면이 안 보낸다 (#89).
   *    계약서 §08 은 아직 발행 시 지정하는 것으로 적혀 있어서, 없다고 막히면 화면이
   *    초대를 아예 못 한다 — 이 테스트가 그 회귀를 잡는다.
   */
  /**
   * ⚠️ `POST /auth/withdraw` 는 **계약서에 없다** (`lib/api/types.ts`). 화면을 끝까지 돌려
   *    보려고 목에만 세운 제안이고, 서버가 붙으면 이 표를 실서버에도 건다.
   */
  it("POST /auth/logout 이 교환 핸들러에 안 먹힌다 — 204 다", async () => {
    // 🚨 `:provider` 가 `logout` 까지 삼켜서 실제로 500 이 나고 있었다. 화면이 로컬 세션을
    //    비우고 나가 버려서 아무도 눈치채지 못한 종류의 회귀라, 여기서 못을 박는다.
    const res = await raw("/auth/logout");
    expect(res.status).toBe(204);
  });

  it("탈퇴는 화면의 읽음 표시를 같이 받는다 — 없으면 막힌다", async () => {
    const caught = await api.post("/auth/withdraw", {}).catch((e) => e);
    expect(isApiError(caught, "validation_failed")).toBe(true);

    // 🚨 유예가 없다 (#167). 응답이 "언제 지워질 예정" 이 아니라 **지워진 시각**이라,
    //    화면이 미래 시각을 받아 날짜를 약속할 방법 자체가 없다.
    const res = await api.post<WithdrawResponse>("/auth/withdraw", {
      acknowledged_immediate_deletion: true,
    });
    expect(new Date(res.deleted_at).getTime()).toBeLessThanOrEqual(Date.now());
  });

  it("relation 없이 초대해도 코드가 나온다", async () => {
    const res = await api.post<InviteResponse>("/children/c1/invites", {});
    expect(res.invite_code).toHaveLength(INVITE_CODE_LENGTH);
  });

  /**
   * 🚨 **정규화를 거쳐도 같은 코드여야 한다.** 발행한 값에 `I` `L` `O` `U` 가 섞여 있으면,
   *    받는 쪽 화면이 `O`→`0` 으로 고쳐 보내는 순간 **서버에 없는 코드**가 된다.
   */
  it("발행된 코드는 화면의 정규화를 통과해도 그대로다", async () => {
    const res = await api.post<InviteResponse>("/children/c1/invites", {});
    expect(normalizeInviteCode(res.invite_code)).toBe(res.invite_code);
  });

  /**
   * 🚨 **발행은 owner 만 한다** (#198). 화면은 member 에게 버튼을 안 그리지만, 낡은 캐시로
   *    시트가 열리면 이 403 을 받아 이유를 말해야 한다 — 연결 없음과 코드가 갈려야 문구가 갈린다.
   */
  it("초대로 들어온 member 가 발행하면 403 owner_only", async () => {
    setScenario("consent");
    try {
      const joined = await api.post<InviteAcceptResponse>("/invites/MKGRAND1/accept", {
        adult_attested: true,
      });
      expect(joined.role).toBe("member");

      const caught = await api.post(`/children/${joined.child_id}/invites`, {}).catch((e) => e);
      expect(isApiError(caught, "owner_only")).toBe(true);
      expect((caught as ApiError).status).toBe(403);
    } finally {
      setScenario("default");
    }
  });

  it("연결되지 않은 아이에 발행하면 403 child_access_denied", async () => {
    const caught = await api.post("/children/c-unknown/invites", {}).catch((e) => e);
    expect(isApiError(caught, "child_access_denied")).toBe(true);
    expect((caught as ApiError).status).toBe(403);
  });
});

/**
 * 초대 수락. 코드 방식은 `docs/api/invite-v1.md` 로 확정됐고, **확인 조회(`GET /invites/{code}`)와
 * 응답 모양은 아직 제안이다** (같은 문서 §7 열린 결정). 서버가 붙으면 이 표를 실서버에도 건다.
 */
describe("⑱ 초대 수락 — 아이는 보호자당 한 명", () => {
  /** 🚨 연결이 성공해야 하는 호출은 전부 만 19세 표시를 싣는다 — 화면이 그렇게 보낸다. */
  const ADULT = { adult_attested: true } as const;

  /**
   * 🚨 **확인은 코드를 쓰지 않는다.** 확인 화면에서 그만둔 사람의 코드가 소비되면, 한 번만
   *    쓸 수 있는 코드라 다시 받아야 한다 — 이 테스트가 그 회귀를 잡는다.
   */
  it("확인은 코드를 소비하지 않는다 — 두 번 확인한 뒤에도 수락된다", async () => {
    setScenario("consent");
    try {
      const first = await api.get<InvitePreviewResponse>("/invites/MKGRAND1");
      expect(first.child.nickname).toBeTruthy();
      await api.get<InvitePreviewResponse>("/invites/MKGRAND1");

      const accepted = await api.post<InviteAcceptResponse>("/invites/MKGRAND1/accept", ADULT);
      expect(accepted.child_id).toBe("c1");
    } finally {
      setScenario("default");
    }
  });

  /**
   * 🚨 **확인도 시도 제한에 걸린다.** 코드를 소비하지 않고 계정 상태도 안 보므로 수락보다
   *    **더 좋은 추측 도구**다 — 여기만 열려 있으면 8자(40비트) 제한이 있으나 마나다.
   */
  it("확인을 여러 번 틀리면 수락까지 429 로 막힌다", async () => {
    setScenario("consent");
    try {
      for (let i = 0; i < 5; i += 1) {
        await api.get("/invites/MKWASTED").catch(() => null);
      }
      const caught = await api.post("/invites/MKGRAND1/accept", {}).catch((e) => e);
      expect(isApiError(caught, "too_many_attempts")).toBe(true);
    } finally {
      setScenario("default");
    }
  });

  /**
   * 🚨 **연결되지 않을 아이의 별명·나이를 보여주지 않는다.** 확인 단계가 막지 않으면
   *    프로필까지 보여주고 나서 수락에서 거절하는 꼴이 된다.
   */
  it("이미 아이가 있으면 확인 단계에서 막힌다", async () => {
    const caught = await api.get("/invites/MKGRAND1").catch((e) => e);
    expect(isApiError(caught, "child_already_exists")).toBe(true);
  });

  /**
   * 🚨 **순서는 아이 보유 → 코드다** (서버 #198). 아이가 있는 보호자에게 틀린 코드를 404 로
   *    답하면 그 응답이 곧 "이 코드는 없다" 는 정보가 된다 — 코드가 맞든 틀리든 같은 409 다.
   */
  it("아이가 있으면 틀린 코드여도 409 다 — 코드의 유효 여부를 알려 주지 않는다", async () => {
    for (const code of ["ABC", "MKWASTED", "MKPAST12"]) {
      const caught = await api.get(`/invites/${code}`).catch((e) => e);
      expect(isApiError(caught, "child_already_exists")).toBe(true);
    }
  });

  /**
   * 🚨 **아이 보유 409 는 시도 제한에 세지 않는다.** 코드와 무관한 호출자 상태라, 세면
   *    아이가 있는 보호자가 화면을 몇 번 오가는 것만으로 스스로 429 에 갇힌다.
   */
  it("아이 보유 409 는 몇 번이어도 429 로 바뀌지 않는다", async () => {
    for (let i = 0; i < 6; i += 1) {
      const caught = await api.get("/invites/MKWASTED").catch((e) => e);
      expect(isApiError(caught, "child_already_exists")).toBe(true);
    }
  });

  it("수락 사이에 아이가 생긴 409 도 실패로 세지 않는다", async () => {
    setScenario("consent");
    try {
      for (let i = 0; i < 6; i += 1) {
        await api.post("/invites/MKTAKEN2/accept", {}).catch(() => null);
      }
      const res = await api.post<InviteAcceptResponse>("/invites/MKGRAND1/accept", ADULT);
      expect(res.child_id).toBe("c1");
    } finally {
      setScenario("default");
    }
  });

  it("받는 쪽이 고른 관계가 보호자 목록에 들어간다", async () => {
    setScenario("consent");
    try {
      await api.post<InviteAcceptResponse>("/invites/MKGRAND1/accept", {
        ...ADULT,
        relation: "sitter",
      });
      const me = await api.get<Me>("/me");
      expect(me.children[0]?.relation).toBe("sitter");
    } finally {
      setScenario("default");
    }
  });

  it("아이가 없는 계정은 코드로 연결된다", async () => {
    setScenario("consent");
    try {
      const res = await api.post<InviteAcceptResponse>("/invites/MKGRAND1/accept", ADULT);
      expect(res.child_id).toBe("c1");
      // 🚨 초대받은 보호자는 owner 가 아니다. 이게 뒤집히면 10 설정에서 남을 끊을 수 있다.
      expect(res.role).toBe("member");

      // 수락하면 **바로** 연결된다 — 승인 대기 상태가 없다 (계약서 §02).
      const me = await api.get<Me>("/me");
      expect(me.children.map((c) => c.child_id)).toContain("c1");
    } finally {
      setScenario("default");
    }
  });

  /**
   * 🚨 **만 19세 표시 없이는 연결되지 않는다** (약관 제7조 ② · #166). 목이 통과시키면 화면이
   *    체크값을 빠뜨리거나 상수로 굳혀도 아무도 모른다.
   */
  it.each([{}, { adult_attested: false }])(
    "만 19세 표시가 없으면 연결되지 않는다 (%o)",
    async (body) => {
      setScenario("consent");
      try {
        const caught = await api.post("/invites/MKGRAND1/accept", body).catch((e) => e);
        expect(isApiError(caught, "validation_failed")).toBe(true);

        const me = await api.get<Me>("/me");
        expect(me.children).toHaveLength(0);

        // 코드를 쓰지 않았다 — 표시를 하고 다시 누르면 그대로 연결된다.
        const res = await api.post<InviteAcceptResponse>("/invites/MKGRAND1/accept", ADULT);
        expect(res.child_id).toBe("c1");
      } finally {
        setScenario("default");
      }
    },
  );

  it("이미 아이가 있으면 수락이 막힌다", async () => {
    // default 시나리오는 아이가 하나 있는 계정이다.
    const caught = await api.post("/invites/MKGRAND1/accept", {}).catch((e) => e);
    expect(isApiError(caught, "child_already_exists")).toBe(true);
  });

  it.each([
    ["MKWASTED", "invite_used"],
    ["MKPAST12", "invite_expired"],
    ["ABC", "invite_not_found"],
  ])("%s 는 %s 로 막힌다", async (code, expected) => {
    setScenario("consent");
    try {
      const caught = await api.post(`/invites/${code}/accept`, {}).catch((e) => e);
      expect(isApiError(caught, expected)).toBe(true);
    } finally {
      setScenario("default");
    }
  });

  /**
   * 🚨 **시도 제한이 8자 코드의 전제다.** 이게 빠지면 40비트를 그냥 긁을 수 있다 —
   *    링크 방식에는 없던 요구사항이라, 서버에 요청한 것을 목이 먼저 지킨다 (#96).
   */
  it("여러 번 틀리면 429 로 막힌다", async () => {
    setScenario("consent");
    try {
      for (let i = 0; i < 5; i += 1) {
        await api.post("/invites/MKWASTED/accept", {}).catch(() => null);
      }
      const caught = await api.post("/invites/MKGRAND1/accept", {}).catch((e) => e);
      expect(isApiError(caught, "too_many_attempts")).toBe(true);
    } finally {
      setScenario("default");
    }
  });
});

/**
 * 가입 흐름. ⚠️ `signup` 의 `nickname` 과 `POST /children` 의 `consents` 둘 다
 * **계약 확정 전이다** (#96).
 */
describe("㉓ 약관은 서버가 정한다 (#90 · #91 · #172)", () => {
  it("동의마다 버전 · 제목 · 필수 여부가 함께 오고, 배열 순서가 화면 순서다", async () => {
    const policies = await api.get<Policy[]>("/policies");

    // 🚨 순서는 서버가 정한다 — 계정 동의 → 아이 동의, 민감정보가 마지막이다.
    expect(policies.map((p) => p.scope)).toEqual([
      "service_terms",
      "privacy_account",
      "location",
      "child_basic",
      "child_health",
    ]);
    for (const policy of policies) {
      expect(policy.version).toBeTruthy();
      expect(policy.label).toBeTruthy();
    }
    // 🚨 **본문은 오지 않는다.** 전문은 html_path 의 정본 페이지다 (멘토 #71-4 · #179).
    expect(policies.every((p) => !("content" in p))).toBe(true);
  });

  /**
   * 🚨 **어느 화면이 묻는지는 서버가 말한다** (#190). 화면이 scope 이름으로 가르던 추측을
   *    이 값으로 바꿨으므로, 목도 실서버와 같은 기준(`ACCOUNT_SCOPES`)으로 답해야 한다.
   */
  it("동의마다 target 이 오고, 가입에 실리는 것과 같은 기준이다", async () => {
    const policies = await api.get<Policy[]>("/policies");
    const byTarget = Object.fromEntries(policies.map((p) => [p.scope, p.target]));

    expect(byTarget).toEqual({
      service_terms: "account",
      privacy_account: "account",
      location: "account",
      child_basic: "child",
      child_health: "child",
    });

    // 🚨 가입은 계정 동의만 받는다 — 그 세 건으로 실제로 가입이 된다.
    const session = await api.post<AuthSession>("/auth/kakao/signup", {
      consent_code: "cc_mock",
      bind: "b",
      nickname: "테스터",
      consents: await agreed(policies.filter((p) => p.target === "account").map((p) => p.scope)),
    });
    expect(session.is_new).toBe(true);
  });

  /** 🚨 필수/선택이 섞여 있다. 화면이 목록 길이로 제출을 막으면 선택까지 필수가 된다. */
  it("location 은 선택이고 child_health 는 민감정보다", async () => {
    const policies = await api.get<Policy[]>("/policies");
    const byScope = new Map(policies.map((p) => [p.scope, p]));

    expect(byScope.get("location")?.required).toBe(false);
    expect(byScope.get("child_health")?.sensitive).toBe(true);
    expect(policies.filter((p) => p.required)).toHaveLength(4);
  });

  /**
   * 🚨 **전문이 없는 자리가 있다.** 아이 동의 둘은 아직 정본이 없어서 `html_path` 가 null 이고,
   *    화면은 그때 전문 보기를 **숨긴다** — 대신 보여줄 글을 만들지 않는다.
   */
  it("아이 동의 둘은 전문 주소가 비어 있다", async () => {
    const policies = await api.get<Policy[]>("/policies");
    const empty = policies.filter((p) => p.html_path === null).map((p) => p.scope);
    expect(empty).toEqual(["child_basic", "child_health"]);

    const terms = policies.find((p) => p.scope === "service_terms");
    // `/api/v1` 아래 경로다 — 절대 주소는 기준 주소 + 이 값 (`policyHref`).
    expect(terms?.html_path).toBe(`/policies/service_terms/${terms?.version}`);
  });

  it("등록되지 않은 버전으로 가입하면 400 이고 계정이 만들어지지 않는다", async () => {
    const caught = await api
      .post("/auth/kakao/signup", {
        consent_code: "cc_mock",
        bind: "b",
        nickname: "테스터",
        consents: [
          { scope: "service_terms", policy_version: "2026-09-01" },
          { scope: "privacy_account", policy_version: "2026-09-01" },
        ],
      })
      .catch((e) => e);

    expect(isApiError(caught, "policy_version_invalid")).toBe(true);
    expect((caught as ApiError).status).toBe(400);
    // 어느 항목이 어긋났는지 말해 준다 — 화면이 그 항목만 다시 받는다.
    expect((caught as ApiError).detail).toMatchObject({ scope: "service_terms" });
  });

  it("아이 등록도 같은 검사를 받는다 — 동의가 어긋난 채로 아이가 생기지 않는다", async () => {
    const caught = await api
      .post("/children", {
        nickname: "테스트",
        birth_date: "2021-04-02",
        consents: [
          { scope: "child_basic", policy_version: "draft-0" },
          { scope: "child_health", policy_version: "옛날판" },
        ],
        guardian_attested: true,
      })
      .catch((e) => e);

    expect(isApiError(caught, "policy_version_invalid")).toBe(true);
    expect((caught as ApiError).detail).toMatchObject({ scope: "child_health" });
  });

  /**
   * 🚨 **받아 간 뒤에 바뀌는 경우**가 화면이 실제로 겪는 것이다 (`policy_bumped` 시나리오).
   *    화면은 다시 받아서 **바뀐 항목만** 체크를 풀고 다시 확인받는다 — 그 뒤 같은 대기표로
   *    가입이 된다. 로그인부터 다시 시키지 않는 것이 이 흐름의 요점이다 (§3-5).
   */
  it("제출 직전에 약관이 바뀌면 400 이고, 다시 받은 버전으로는 가입된다", async () => {
    setScenario("policy_bumped");
    try {
      const stale = await agreed(["service_terms", "privacy_account"]);

      const caught = await api
        .post("/auth/kakao/signup", {
          consent_code: "cc_mock",
          bind: "b",
          nickname: "테스터",
          consents: stale,
        })
        .catch((e) => e);
      expect(isApiError(caught, "policy_version_invalid")).toBe(true);

      const fresh = await agreed(["service_terms", "privacy_account"]);
      // 바뀐 것만 버전이 다르다 — 화면에서 체크가 풀리는 것도 이 항목뿐이다.
      expect(fresh[0]?.policy_version).not.toBe(stale[0]?.policy_version);
      expect(fresh[1]?.policy_version).toBe(stale[1]?.policy_version);

      const session = await api.post<AuthSession>("/auth/kakao/signup", {
        consent_code: "cc_mock",
        bind: "b",
        nickname: "테스터",
        consents: fresh,
      });
      expect(session.is_new).toBe(true);
    } finally {
      setScenario("default");
    }
  });
});

describe("⑲ 가입 — 동의가 빠지면 아무것도 만들어지지 않는다", () => {
  it("계정 동의가 빠지면 signup 이 403 이다", async () => {
    const caught = await api
      .post("/auth/kakao/signup", {
        consent_code: "cc_mock",
        bind: "b",
        nickname: "테스터",
        consents: await agreed(["service_terms"]),
      })
      .catch((e) => e);
    expect(isApiError(caught, "consent_required")).toBe(true);
  });

  /**
   * 🚨 **선택 동의가 제출을 막지 않는다.** 가입 화면에 `location` 이 서면서 이 구분이
   *    실제로 갈리는 자리가 됐다 — 목록 길이로 세는 구현이면 여기서 403 이 난다.
   */
  it("선택 동의를 안 골라도 가입된다", async () => {
    const res = await api.post<AuthSession>("/auth/kakao/signup", {
      consent_code: "cc_mock",
      bind: "b",
      nickname: "테스터",
      consents: await agreed(["service_terms", "privacy_account"]),
    });
    expect(res.is_new).toBe(true);

    // 안 고른 것은 켜져 있지 않다 — 화면이 물어본 것과 서버에 남는 것이 같아야 한다.
    const consents = await api.get<ConsentsResponse>("/consents", { query: { child_id: "c1" } });
    expect(consents.effective.location).toBe(false);
  });

  it("가입에서 켠 선택 동의는 10 설정에도 켜져 있다", async () => {
    await api.post<AuthSession>("/auth/kakao/signup", {
      consent_code: "cc_mock",
      bind: "b",
      nickname: "테스터",
      consents: await agreed(["service_terms", "privacy_account", "location"]),
    });

    const consents = await api.get<ConsentsResponse>("/consents", { query: { child_id: "c1" } });
    expect(consents.effective.location).toBe(true);
    expect(consents.history.some((h) => h.scope === "location" && h.action === "granted")).toBe(
      true,
    );
  });

  it("이름이 없으면 signup 이 막힌다", async () => {
    const caught = await api
      .post("/auth/kakao/signup", {
        consent_code: "cc_mock",
        bind: "b",
        consents: await agreed(["service_terms", "privacy_account"]),
      })
      .catch((e) => e);
    expect(isApiError(caught, "validation_failed")).toBe(true);
  });

  /**
   * 🚨 **아이와 아이 동의는 한 트랜잭션이다.** 동의 없이 아이가 만들어지면 그 아이의
   *    기록은 근거 없는 수집이 된다 (계약서 §04 "동의는 저장보다 먼저다").
   */
  it("아이 동의가 빠지면 POST /children 이 403 이다", async () => {
    const caught = await api
      .post("/children", {
        nickname: "테스트",
        birth_date: "2021-04-02",
        consents: await agreed(["child_basic"]),
        guardian_attested: true,
      })
      .catch((e) => e);
    expect(isApiError(caught, "consent_required")).toBe(true);
  });

  it("법정대리인 확인이 없으면 POST /children 이 403 이다", async () => {
    const caught = await api
      .post("/children", {
        nickname: "테스트",
        birth_date: "2021-04-02",
        consents: await agreed(["child_basic", "child_health"]),
        guardian_attested: false,
      })
      .catch((e) => e);
    expect(isApiError(caught, "consent_required")).toBe(true);
  });
});

/**
 * 11 아이 프로필. ⚠️ 여기서 거는 경로 셋(`GET`/`PATCH /children/{cid}` · `/growth`)은
 * **계약서 v1 에 없다** (이슈 #75). 목이 그 제안된 계약대로 행동하는지를 걸어 둬서,
 * 서버가 붙을 때 같은 표를 실서버에도 그대로 옮길 수 있게 한다.
 */
describe("⑫ 아이 프로필은 고친 것만 덮는다", () => {
  it("PATCH 는 보낸 필드만 바꾸고 나머지는 그대로 둔다", async () => {
    const before = await api.get<ChildProfile>("/children/c1");

    const { child } = await api.patch<{ child: ChildProfile }>("/children/c1", {
      nickname: "민서",
    });

    expect(child.nickname).toBe("민서");
    // 🚨 안 보낸 필드가 조용히 비워지면, 두 보호자가 같은 화면을 열어 뒀을 때 남의 수정이 사라진다.
    expect(child.birth_date).toBe(before.birth_date);
    expect(child.gender).toBe(before.gender);

    const after = await api.get<ChildProfile>("/children/c1");
    expect(after.nickname).toBe("민서");
  });

  it("빈 별명은 422 다 — 이름 없는 아이를 만들지 않는다", async () => {
    await expect(api.patch("/children/c1", { nickname: "   " })).rejects.toSatisfy((e: unknown) =>
      isApiError(e, "validation_failed"),
    );
  });
});

describe("⑬ 측정 기록은 승인 게이트가 아니다", () => {
  it("Idempotency-Key 없이도 저장된다 (되돌릴 수 있는 것이라 표에 없다)", async () => {
    const { items: before } = await api.get<GrowthLogsResponse>("/children/c1/growth");

    await api.post("/children/c1/growth", { measured_on: "2026-09-10", height_cm: 105 });

    const { items: after } = await api.get<GrowthLogsResponse>("/children/c1/growth");
    expect(after.length).toBe(before.length + 1);
    // 🚨 날짜 문구는 서버가 만든다 — 프론트가 계산해 채우지 않는다 (CLAUDE.md §3).
    expect(after[0].measured_label).toBeTypeOf("string");
  });

  it("한쪽만 재고 온 날을 받는다", async () => {
    const { log } = await api.post<{ log: { height_cm: number | null; weight_kg: number | null } }>(
      "/children/c1/growth",
      { measured_on: "2026-09-11", weight_kg: 17.4 },
    );
    expect(log.height_cm).toBeNull();
    expect(log.weight_kg).toBe(17.4);
  });

  it("둘 다 비어 있으면 422 다", async () => {
    await expect(api.post("/children/c1/growth", { measured_on: "2026-09-12" })).rejects.toSatisfy(
      (e: unknown) => isApiError(e, "validation_failed"),
    );
  });

  it("PATCH 는 보낸 필드만 바꾸고 나머지는 그대로 둔다", async () => {
    const { items } = await api.get<GrowthLogsResponse>("/children/c1/growth");
    const target = items.find((log) => log.height_cm !== null && log.weight_kg !== null)!;

    const { log } = await api.patch<{ log: GrowthLog }>(`/children/c1/growth/${target.id}`, {
      height_cm: 111.1,
    });

    expect(log.height_cm).toBe(111.1);
    // 🚨 안 보낸 필드가 조용히 비워지면, 두 보호자가 같은 화면을 열어 뒀을 때 남의 수정이 사라진다.
    expect(log.weight_kg).toBe(target.weight_kg);
    expect(log.measured_on).toBe(target.measured_on);
  });

  it("null 은 '그날 그건 안 잰 것' 이다 — 키를 안 보내는 것과 다르다", async () => {
    const { items } = await api.get<GrowthLogsResponse>("/children/c1/growth");
    const target = items.find((log) => log.height_cm !== null && log.weight_kg !== null)!;

    const { log } = await api.patch<{ log: GrowthLog }>(`/children/c1/growth/${target.id}`, {
      height_cm: null,
    });

    expect(log.height_cm).toBeNull();
    expect(log.weight_kg).toBe(target.weight_kg);
  });

  it("고친 결과가 둘 다 비면 422 다 — 잰 것이 없는 기록을 만들지 않는다", async () => {
    const { items } = await api.get<GrowthLogsResponse>("/children/c1/growth");
    const target = items.find((log) => log.height_cm !== null && log.weight_kg !== null)!;

    await expect(
      api.patch(`/children/c1/growth/${target.id}`, { height_cm: null, weight_kg: null }),
    ).rejects.toSatisfy((e: unknown) => isApiError(e, "validation_failed"));
  });

  it("날짜를 고치면 서버가 날짜 문구도 다시 만든다", async () => {
    const { items } = await api.get<GrowthLogsResponse>("/children/c1/growth");
    const target = items[items.length - 1];

    const { log } = await api.patch<{ log: GrowthLog }>(`/children/c1/growth/${target.id}`, {
      measured_on: toISODate(new Date()),
    });

    // 🚨 프론트가 계산해 채우지 않는다 (CLAUDE.md §3) — 목이 서버 역할을 한다.
    expect(log.measured_label).toBe("오늘");
  });

  it("이미 지워진 줄을 고치면 404 다", async () => {
    await expect(api.patch("/children/c1/growth/g_nope", { height_cm: 100 })).rejects.toSatisfy(
      (e: unknown) => isApiError(e, "not_found"),
    );
  });

  it("지우면 목록에서 빠진다", async () => {
    const { items } = await api.get<GrowthLogsResponse>("/children/c1/growth");
    const target = items[0];

    await api.delete(`/children/c1/growth/${target.id}`);

    const { items: after } = await api.get<GrowthLogsResponse>("/children/c1/growth");
    expect(after.some((log) => log.id === target.id)).toBe(false);
  });
});

/**
 * 🚨 승인 게이트 ㉡. 등록은 `lib/api/operations.ts` 의 전용 함수로만 부를 수 있고(키가 필수 인자),
 *    회수는 행을 지우는 것이 아니라 `retracted` 로 내리는 것이다 (계약서 §10).
 */
describe("⑭ 알레르기는 등록한 것이 목록에 서고, 내리면 빠진다", () => {
  it("등록한 항목이 GET 에 그대로 나온다", async () => {
    const before = await api.get<HealthSafetyListResponse>("/children/c1/health-safety");

    await addHealthSafety(
      "c1",
      { type: "allergy", label: "땅콩", category: "식품" },
      newIdempotencyKey(),
    );

    const after = await api.get<HealthSafetyListResponse>("/children/c1/health-safety");
    expect(after.items.length).toBe(before.items.length + 1);
    expect(after.items.some((item) => item.label === "땅콩")).toBe(true);
  });

  it("같은 항목을 새 키로 또 등록하면 409 다 (재시도와 다른 경로)", async () => {
    await addHealthSafety(
      "c1",
      { type: "allergy", label: "땅콩", category: "식품" },
      newIdempotencyKey(),
    );

    await expect(
      addHealthSafety(
        "c1",
        { type: "allergy", label: "땅콩", category: "식품" },
        newIdempotencyKey(),
      ),
    ).rejects.toSatisfy((e: unknown) => isApiError(e, "already_exists"));
  });

  it("내린 항목은 목록에서 빠지고, 같은 항목을 다시 등록할 수 있다", async () => {
    const before = await api.get<HealthSafetyListResponse>("/children/c1/health-safety");
    const target = before.items[0];

    await api.delete(`/children/c1/health-safety/${target.id}`);

    const after = await api.get<HealthSafetyListResponse>("/children/c1/health-safety");
    expect(after.items.some((item) => item.id === target.id)).toBe(false);

    // 내려간 뒤에는 같은 라벨이 다시 등록돼야 한다 — 회수가 "영영 못 쓰는 이름" 을 만들면 안 된다.
    await expect(
      addHealthSafety(
        "c1",
        { type: target.type, label: target.label, category: target.category },
        newIdempotencyKey(),
      ),
    ).resolves.toBeDefined();
  });

  it("이미 내려간 항목을 또 내리면 404 다", async () => {
    const { items } = await api.get<HealthSafetyListResponse>("/children/c1/health-safety");
    const target = items[0];

    await api.delete(`/children/c1/health-safety/${target.id}`);

    await expect(api.delete(`/children/c1/health-safety/${target.id}`)).rejects.toSatisfy(
      (e: unknown) => isApiError(e, "not_found"),
    );
  });
});

/**
 * 🚨 승인 게이트 ㉡ — 고치기. ⚠️ 계약서 v1 에 없다 (이슈 #87).
 *
 * 여기서 거는 것은 **정체를 못 바꾼다는 것**이다. `type`·`label` 이 바뀌면
 * `UNIQUE(child_id, type, label)` 과 "이미 등록된 항목" 판정이 같이 흔들린다.
 */
describe("⑯ 안전 정보는 정체를 못 바꾼다", () => {
  async function first(): Promise<HealthSafety> {
    const { items } = await api.get<HealthSafetyListResponse>("/children/c1/health-safety");
    return items[0];
  }

  it("심각도·증상·메모는 고쳐진다", async () => {
    const before = await first();

    const { safety } = await api.patch<{ safety: HealthSafety }>(
      `/children/c1/health-safety/${before.id}`,
      { severity: "severe", reactions: ["기침"], notes: "병원에서 다시 확인" },
    );

    expect(safety.severity).toBe("severe");
    expect(safety.reactions).toEqual(["기침"]);
    // 🚨 정체는 그대로다.
    expect(safety.type).toBe(before.type);
    expect(safety.label).toBe(before.label);
  });

  it("🚨 종류·이름을 보내면 400 이다", async () => {
    const target = await first();

    await expect(
      api.patch(`/children/c1/health-safety/${target.id}`, { label: "땅콩" }),
    ).rejects.toSatisfy((e: unknown) => isApiError(e, "validation_failed"));
    await expect(
      api.patch(`/children/c1/health-safety/${target.id}`, { type: "condition" }),
    ).rejects.toSatisfy((e: unknown) => isApiError(e, "validation_failed"));
  });

  it("`severity: null` 은 모르겠어요 로 되돌리는 것이다", async () => {
    const target = await first();
    const { safety } = await api.patch<{ safety: HealthSafety }>(
      `/children/c1/health-safety/${target.id}`,
      { severity: null },
    );
    expect(safety.severity).toBeNull();
  });

  it("내려간 기록은 고칠 수 없다", async () => {
    const target = await first();
    await api.delete(`/children/c1/health-safety/${target.id}`);

    await expect(
      api.patch(`/children/c1/health-safety/${target.id}`, { severity: "mild" }),
    ).rejects.toSatisfy((e: unknown) => isApiError(e, "not_found"));
  });

  it("동의가 없으면 고치지도 못한다", async () => {
    const target = await first();
    setScenario("consent");
    await expect(
      api.patch(`/children/c1/health-safety/${target.id}`, { severity: "mild" }),
    ).rejects.toSatisfy((e: unknown) => isApiError(e, "consent_required"));
    setScenario("default");
  });
});

/**
 * 🚨 11 알레르기 검사지 읽기. ⚠️ 계약서 v1 에 없다 (이슈 #86).
 *
 * 여기서 거는 것은 **읽기가 저장이 아니라는 것**과 **못 읽은 칸을 채워 보내지 않는다는 것**이다.
 * 최상위 `CLAUDE.md` §2 가 "LLM 이 건강 정보를 생성·추론하지 않는다" 를 못박았는데, 그 규칙이
 * 지켜지는지는 타입이 못 본다 — `null` 을 허용한다는 것과 실제로 `null` 을 보낸다는 것은 다르다.
 */
describe("⑮ 검사지 읽기는 저장이 아니다", () => {
  async function scan(): Promise<SafetyScanResponse> {
    const form = new FormData();
    form.append("photo", new Blob([new Uint8Array([1, 2, 3])], { type: "image/png" }), "sheet.png");
    return api.post<SafetyScanResponse>("/children/c1/health-safety/scan", form);
  }

  it("읽어도 안전 정보 목록이 늘지 않는다", async () => {
    const before = await api.get<HealthSafetyListResponse>("/children/c1/health-safety");

    const result = await scan();
    expect(result.candidates.length).toBeGreaterThan(0);

    const after = await api.get<HealthSafetyListResponse>("/children/c1/health-safety");
    expect(after.items.length).toBe(before.items.length);
  });

  it("Idempotency-Key 없이 부를 수 있다 — 저장하는 것이 없어서 표에 없다", async () => {
    await expect(scan()).resolves.toBeDefined();
  });

  it("🚨 못 읽은 칸을 채워 보내지 않는다", async () => {
    const { candidates } = await scan();

    // 목이 일부러 덜 읽은 줄을 섞어 둔다 — 완벽하게 읽어 주면 화면의 빈 칸 처리를 확인할 수 없다.
    expect(candidates.some((c) => c.category === null)).toBe(true);
    expect(candidates.some((c) => c.severity === null)).toBe(true);
    // 🚨 원문 없이 옮겨 적은 줄이 있어야 화면이 그 줄을 미리 고르지 않는지 확인할 수 있다.
    expect(candidates.some((c) => c.source_text === null)).toBe(true);
  });

  it("🚨 읽지 못한 줄 수를 그대로 내린다", async () => {
    const { unreadable_count } = await scan();
    expect(unreadable_count).toBeGreaterThan(0);
  });

  it("승인해야 저장된다 — 고른 줄 수만큼 게이트 ㉡ 를 부른다", async () => {
    const { candidates } = await scan();
    const before = await api.get<HealthSafetyListResponse>("/children/c1/health-safety");

    // 화면이 하는 것과 같다: 필수 칸이 다 찬 줄만, 줄마다 키 하나로.
    const complete = candidates.filter((c) => c.label !== null && c.category !== null);
    for (const candidate of complete) {
      if (before.items.some((item) => item.label === candidate.label)) continue;
      await addHealthSafety(
        "c1",
        { type: "allergy", label: candidate.label!, category: candidate.category! },
        newIdempotencyKey(),
      );
    }

    const after = await api.get<HealthSafetyListResponse>("/children/c1/health-safety");
    expect(after.items.length).toBeGreaterThan(before.items.length);
  });

  it("동의가 없으면 읽지도 못한다 — 저장 전에 막힌다", async () => {
    setScenario("consent");
    await expect(scan()).rejects.toSatisfy((e: unknown) => isApiError(e, "consent_required"));
    setScenario("default");
  });
});

/**
 * 08 사진 — **승인 전에는 아무것도 저장되지 않는다.**
 *
 * 이 블록이 지키는 것은 문구가 아니라 **행동**이다. 화면이 "승인 전에는 저장되지 않아요" 라고
 * 쓰는데 목이 업로드만으로 관찰을 쌓으면, 화면은 거짓말을 하면서도 통과한다.
 */
describe("⑰ 08 사진 — 읽기와 저장이 갈린다", () => {
  /** 실제 이미지 바이트가 필요하지 않다. 목이 보는 것은 파트 이름과 크기다. */
  function fakePhoto(name = "notice.jpg"): File {
    return new File([new Uint8Array([1, 2, 3, 4])], name, { type: "image/jpeg" });
  }

  /** 스트림을 끝까지 돌려서 `parsed` 를 꺼낸다. */
  async function parsedOf(runId: string): Promise<ParsedEvent> {
    const events = [];
    for await (const e of streamRunEvents(runId)) events.push(e);
    const parsed = events.find((e) => e.type === "parsed");
    if (!parsed) throw new Error("parsed 이벤트가 오지 않았다");
    return parsed.data as ParsedEvent;
  }

  /** 🚨 부모가 고치기 시트에서 확인한 것과 같은 모양 — 확인된 항목만 commit 에 실린다. */
  function checked(entries: PhotoEntry[]): PhotoEntry[] {
    return entries.filter((e) => !e.needs_review);
  }

  async function upload(lane: PhotoLane = "document", date?: string): Promise<string> {
    const { run_id } = await uploadPhoto(
      "c1",
      photoFormData(fakePhoto(), { lane, date }),
      newIdempotencyKey(),
    );
    return run_id;
  }

  it("업로드는 lane · parsed 를 흘려보내고 saved 는 보내지 않는다", async () => {
    const runId = await upload();

    const seen: string[] = [];
    for await (const event of streamRunEvents(runId)) seen.push(event.type);

    expect(seen).toContain("lane");
    expect(seen).toContain("parsed");
    // 🚨 여기가 04 한 줄 입력 run 과 갈리는 지점이다.
    expect(seen).not.toContain("saved");
    expect(seen.indexOf("lane")).toBeLessThan(seen.indexOf("parsed"));
    expect(seen.at(-1)).toBe("done");
  });

  it("업로드만으로는 관찰이 늘지 않는다 — commit 이 저장한다", async () => {
    const before = await api.get<ObservationsResponse>("/children/c1/observations");

    const runId = await upload();
    const parsed = await parsedOf(runId);

    const between = await api.get<ObservationsResponse>("/children/c1/observations");
    expect(between.total).toBe(before.total);

    const saved = await commitPhotoRun(runId, {
      lane: "document",
      entries: checked(parsed.entries ?? []),
      attach_to_calendar: true,
    });
    expect(saved.observations).toHaveLength(1);
  });

  it("알림장 한 장에서 항목이 여러 개 나오고, 못 읽은 것이 섞여 온다", async () => {
    const parsed = await parsedOf(await upload());
    const entries = parsed.entries ?? [];

    // 🚨 계약서 v1 의 `extracted`(항목 하나)로는 담을 수 없는 모양이다 — 이 테스트가 그 사실이다.
    expect(entries.length).toBeGreaterThan(1);
    expect(entries.some((e) => e.needs_review)).toBe(true);
    // 🚨 못 읽은 날짜는 `null` 이다. 서버가 오늘로 채우지 않는다.
    const unread = entries.find((e) => e.needs_review)!;
    expect(unread.date).toBeNull();
    expect(unread.review_reason).toBeTruthy();
  });

  it("확인하지 않은 항목을 보내면 422 다 — 승인 전 저장을 서버도 막는다", async () => {
    const runId = await upload();
    const parsed = await parsedOf(runId);

    await expect(
      commitPhotoRun(runId, {
        lane: "document",
        // 화면이라면 빼고 보냈을 것을 일부러 그대로 보낸다.
        entries: parsed.entries ?? [],
        attach_to_calendar: true,
      }),
    ).rejects.toSatisfy((error: unknown) => isApiError(error, "validation_failed"));
  });

  it("확인한 항목만 저장된다", async () => {
    const runId = await upload();
    const parsed = await parsedOf(runId);
    const only = checked(parsed.entries ?? []);

    const saved = await commitPhotoRun(runId, {
      lane: "document",
      entries: only,
      attach_to_calendar: false,
    });

    expect(saved.observations[0]?.domain_fields.entries).toBe(only.length);
    expect(only.length).toBeLessThan((parsed.entries ?? []).length);
  });

  it("문서에서 읽은 것은 institution_notice 로 고정이고 프로필로 승격하지 않는다", async () => {
    const runId = await upload();
    const parsed = await parsedOf(runId);

    const saved = await commitPhotoRun(runId, {
      lane: "document",
      entries: checked(parsed.entries ?? []),
      attach_to_calendar: true,
    });

    const observation = saved.observations[0];
    // 🚨 기관 공지가 보호자 발화로 들어가면 출처 추적이 끊긴다 (계약서 §09).
    expect(observation.confidence_source).toBe("institution_notice");
    expect("affinity" in observation && observation.affinity).toBeNull();
  });

  it("🚨 커밋은 일정을 만들지 않는다 — 관찰만 저장한다", async () => {
    const runId = await upload();
    const parsed = await parsedOf(runId);

    const saved: PhotoCommitResponse = await commitPhotoRun(runId, {
      lane: "document",
      entries: checked(parsed.entries ?? []),
      attach_to_calendar: false,
    });

    /**
     * 🚨 `event.status` 가 없어진 뒤(#118) 이 커밋이 **게이트 없이 캘린더에 쓰는 유일한 경로**
     *    였다. 일정을 아예 안 만들면 그 경로가 사라진다 — 일정은 고치기 시트의 승인 게이트가 넣는다.
     */
    expect(saved).not.toHaveProperty("drafts");
    expect(saved).not.toHaveProperty("event");
    expect(saved.observations.length).toBeGreaterThan(0);
  });

  it("한 달치 식단표도 항목 배열로 온다 — 화면이 접어 두는 이유", async () => {
    setScenario("photo_meal_plan");
    try {
      const parsed = await parsedOf(await upload());
      expect((parsed.entries ?? []).length).toBeGreaterThan(20);
    } finally {
      setScenario("default");
    }
  });

  it("활동 사진은 화면이 들고 온 날짜로 캘린더에 붙는다", async () => {
    const runId = await upload("activity", "2026-09-12");
    for await (const _ of streamRunEvents(runId)) void _;

    const saved = await commitPhotoRun(runId, {
      lane: "activity",
      selected_tags: ["블록 쌓기"],
      attach_to_calendar: true,
    });

    expect(saved.calendar_date).toBe("2026-09-12");
    // 🚨 사진 태그는 성향으로 확정하지 않는다 — affinity 를 붙이지 않는다 (계약서 §09).
    const observation = saved.observations[0];
    expect(observation.confidence_source).toBe("parent_hearsay");
    expect("affinity" in observation && observation.affinity).toBeNull();
  });

  it("고른 lane 이 읽어낼 것을 정한다 — 시나리오 없이 두 갈래가 다 나온다", async () => {
    const docRun = await upload("document");
    const docEvents = [];
    for await (const e of streamRunEvents(docRun)) docEvents.push(e);
    const docParsed = docEvents.find((e) => e.type === "parsed")!.data as ParsedEvent;
    expect(docParsed.raw_text).not.toBe("");

    const actRun = await upload("activity");
    const actEvents = [];
    for await (const e of streamRunEvents(actRun)) actEvents.push(e);
    const actParsed = actEvents.find((e) => e.type === "parsed")!.data as ParsedEvent;
    // 활동 사진에는 읽을 글자가 없다.
    expect(actParsed.raw_text).toBe("");
    // 🚨 문서는 `entries`, 활동은 `tags` — 다른 필드다 (한 배열에 섞지 않는다).
    expect((actParsed.tags ?? []).length).toBeGreaterThan(0);
    expect(actParsed.entries ?? []).toHaveLength(0);
    expect((docParsed.entries ?? []).length).toBeGreaterThan(0);
  });

  it("lane 없이 올리면 422 다 — 서버가 저장 경로를 추측하게 두지 않는다", async () => {
    const form = new FormData();
    form.append("file", fakePhoto());

    await expect(uploadPhoto("c1", form, newIdempotencyKey())).rejects.toSatisfy((error: unknown) =>
      isApiError(error, "validation_failed"),
    );
  });

  it("어긋나게 읽혀도 서버는 부모가 고른 lane 을 덮지 않는다", async () => {
    setScenario("photo_lane_mismatch");
    try {
      const runId = await upload("document");
      const seen = [];
      for await (const e of streamRunEvents(runId)) seen.push(e);

      const lane = seen.find((e) => e.type === "lane")!.data as LaneEvent;
      // 🚨 서버는 "다르게 읽혔다" 만 알린다. 무엇으로 저장할지는 commit 의 `lane` 이 정하고,
      //    그 값은 화면이 부모가 고른 것으로 채운다.
      expect(lane.guess).toBe("activity");

      const parsed = seen.find((e) => e.type === "parsed")!.data as ParsedEvent;
      const saved = await commitPhotoRun(runId, {
        lane: "document",
        entries: checked(parsed.entries ?? []),
        attach_to_calendar: false,
      });
      expect(saved.observations[0].confidence_source).toBe("institution_notice");
    } finally {
      setScenario("default");
    }
  });

  it("읽어낼 게 없는 사진은 failed 로 끝나고 아무것도 저장하지 않는다", async () => {
    setScenario("photo_unreadable");
    try {
      const before = await api.get<ObservationsResponse>("/children/c1/observations");
      const runId = await upload();

      const seen: string[] = [];
      for await (const event of streamRunEvents(runId)) seen.push(event.type);

      expect(seen.at(-1)).toBe("failed");
      expect(seen).not.toContain("parsed");

      const after = await api.get<ObservationsResponse>("/children/c1/observations");
      expect(after.total).toBe(before.total);
    } finally {
      setScenario("default");
    }
  });

  it("고른 것도 없고 올릴 날짜도 없으면 422 다 — 빈 저장을 만들지 않는다", async () => {
    const runId = await upload();
    for await (const _ of streamRunEvents(runId)) void _;

    await expect(
      commitPhotoRun(runId, { lane: "document", entries: [], attach_to_calendar: false }),
    ).rejects.toSatisfy((error: unknown) => isApiError(error, "validation_failed"));
  });
});

/** 목이 만든 달을 그대로 물어보기 위한 키. 표시가 아니라 쿼리 파라미터다. */
function monthOf(date: Date): string {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}`;
}

/**
 * 일정이 걸린 날 하나. 목의 확정 일정은 **다가오는 것 하나**(모레)라, 이번 달만 물어보면
 * **매달 마지막 이틀에는 그 일정이 다음 달에 있어서** 빈 달이 돌아온다. 실제로 9/29 에
 * 깨졌다 — 화면 버그가 아니라 "이번 달에 일정이 있다" 를 테스트가 전제한 것이 문제였다.
 *
 * 🚨 **픽스처를 import 해서 날짜를 가져오지 않는다.** 이 파일은 목을 밖에서 부르는 계약
 *    테스트라, 목이 들고 있는 값을 직접 읽으면 "계약대로 답하는가" 가 아니라 "픽스처와
 *    같은가" 를 재게 된다. 달을 하나 더 물어보는 쪽이 부르는 사람의 방법이다.
 */
async function findEventDay(): Promise<CalendarMonthResponse["days"][number]> {
  const now = new Date();
  const months = [monthOf(now), monthOf(new Date(now.getFullYear(), now.getMonth() + 1, 1))];

  for (const month of months) {
    const res = await api.get<CalendarMonthResponse>("/children/c1/calendar", {
      query: { month },
    });
    const day = res.days.find((d) => d.has_event);
    if (day) return day;
  }
  throw new Error(`일정이 걸린 날이 ${months.join(" · ")} 어디에도 없다`);
}
