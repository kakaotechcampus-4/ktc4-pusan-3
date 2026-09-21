import { describe, expect, it } from "vitest";

import { API_BASE_URL } from "@/lib/env";
import { ApiError, isApiError } from "@/lib/api/errors";
import { idempotentPath, newIdempotencyKey } from "@/lib/api/idempotency";
import {
  addHealthSafety,
  commitPhotoRun,
  confirmEvent,
  photoFormData,
  submitOnboarding,
  uploadPhoto,
} from "@/lib/api/operations";
import { streamRunEvents, type LaneEvent, type ParsedEvent } from "@/lib/api/sse";
import { toISODate } from "@/lib/format";
import { api } from "@/lib/api/client";
import type {
  Affinity,
  AffinitiesResponse,
  AuthSession,
  ChildParentsResponse,
  ConsentResponse,
  ConsentsResponse,
  InviteAcceptResponse,
  InviteResponse,
  Me,
  WithdrawResponse,
  CalendarDayResponse,
  CalendarMonthResponse,
  ChildProfile,
  CorrectionResponse,
  GrowthLog,
  GrowthLogsResponse,
  HealthSafety,
  HealthSafetyListResponse,
  Observation,
  ObservationsResponse,
  PhotoCommitResponse,
  PhotoEntry,
  PhotoLane,
  SafetyScanResponse,
  SuggestionFeedbackResponse,
} from "@/lib/api/types";

import { INVITE_CODE_LENGTH, normalizeInviteCode } from "@/lib/invite-code";

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

/** 목이 계약서 경로를 그대로 쓰는지 확인하려면 URL 을 직접 만들어야 할 때가 있다. */
function raw(path: string, init?: RequestInit): Promise<Response> {
  return fetch(`${API_BASE_URL}${path}`, { method: "POST", ...init });
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

    const first = await confirmEvent("e_1", key);
    const second = await confirmEvent("e_1", key);

    expect(first.event.status).toBe("confirmed");
    expect(second).toEqual(first);

    // 두 번째가 409 로 오면 "성공했는데 응답을 못 받은" 경우가 실패처럼 보인다 — 그걸 막는 줄이다.
    expect(second.event.id).toBe("e_1");
  });
});

describe("③ 같은 키 · 다른 요청 = 재사용 거부", () => {
  it("422 idempotency_key_reuse", async () => {
    const key = newIdempotencyKey();
    await submitOnboarding("c1", { interests: ["공룡"] }, key);

    await expect(submitOnboarding("c1", { interests: ["물놀이"] }, key)).rejects.toSatisfy(
      (e: unknown) => isApiError(e, "idempotency_key_reuse") && e.status === 422,
    );
  });

  it("다른 엔드포인트에 같은 키를 돌려써도 거부한다", async () => {
    const key = newIdempotencyKey();
    await confirmEvent("e_2", key);

    await expect(
      addHealthSafety("c1", { type: "allergy", label: "지어낸항목", category: "식품" }, key),
    ).rejects.toSatisfy((e: unknown) => isApiError(e, "idempotency_key_reuse"));
  });
});

describe("④ 같은 키 · 동시 요청", () => {
  it("한 번만 실행되고 나머지는 409 idempotency_in_progress", async () => {
    const key = newIdempotencyKey();

    const results = await Promise.allSettled([
      confirmEvent("e_3", key),
      confirmEvent("e_3", key),
      confirmEvent("e_3", key),
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
  it("새 키로 이미 확정된 일정을 또 확정하면 409 already_confirmed", async () => {
    await confirmEvent("e_4", newIdempotencyKey());

    // 같은 이벤트, 새 사용자 동작(= 새 키). 이건 재시도가 아니라 중복 확정 시도다.
    await expect(confirmEvent("e_4", newIdempotencyKey())).rejects.toSatisfy(
      (e: unknown) => isApiError(e, "already_confirmed") && e.status === 409,
    );
  });

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
        await submitOnboarding("c1", { interests: ["공룡"] }, key);
      } catch (e) {
        caught = e;
      }

      expect(isApiError(caught, "consent_required")).toBe(true);
      expect((caught as ApiError).status).toBe(403);
      expect((caught as ApiError).consentDeeplink).toBe("settings/consents");

      // 🚨 거부는 "처리 결과" 가 아니다. 동의를 받은 뒤 같은 키로 다시 보내면 통과해야 한다.
      setScenario("default");
      const retried = await submitOnboarding("c1", { interests: ["공룡"] }, key);
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

/* ── 07 기억 · 교정 ──────────────────────────────────────────────────── */

describe("⑧ 관찰과 프로필은 다른 엔드포인트다", () => {
  it("도메인을 생략하면 4개 테이블을 병합해 observed_to DESC 로 내려준다", async () => {
    const page = await api.get<ObservationsResponse>("/children/c1/observations");

    const kinds = new Set(page.items.map((item) => item.kind));
    expect(kinds.size).toBeGreaterThan(1);
    expect(kinds).toContain("observation_health");
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

  it("프로필은 affinities 한 배열이고 safety 가 따로 온다", async () => {
    const body = await api.get<AffinitiesResponse>("/children/c1/affinities");

    expect(Array.isArray(body.affinities)).toBe(true);
    // 🚨 기억이 비어도 안전 정보는 비지 않는다 — 감쇠가 없다 (계약서 §07).
    expect(body.safety.length).toBeGreaterThan(0);
  });

  it("health 관찰에는 subject · polarity · affinity 키가 아예 없다", async () => {
    const page = await api.get<ObservationsResponse>("/children/c1/observations", {
      query: { domain: "health" },
    });
    const health = page.items[0];

    expect(health.kind).toBe("observation_health");
    expect("subject" in health).toBe(false);
    expect("polarity" in health).toBe(false);
    expect("affinity" in health).toBe(false);
  });
});

describe("⑨ 교정은 지우지 않고 내린다", () => {
  it("target_ref 를 배열로 보내면 422 다", async () => {
    const response = await fetch(`${API_BASE_URL}/corrections`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        target_ref: [{ kind: "profile_affinity", id: "a_12" }],
        verdict: "confirm",
        child_id: "c1",
      }),
    });

    expect(response.status).toBe(422);
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
    // 기본 조회는 active 만 본다 — 행은 남았지만 목록에서는 빠진다.
    const after = await api.get<ObservationsResponse>("/children/c1/observations");
    expect(after.items.some((item) => item.id === target!.id)).toBe(false);
  });

  it("기억 need_more_observation 은 confirmed 를 candidate 로 한 단계만 내린다", async () => {
    const result = await api.post<CorrectionResponse>("/corrections", {
      target_ref: { kind: "profile_affinity", id: "a_12" },
      verdict: "need_more_observation",
      child_id: "c1",
    });

    expect((result.target as Affinity).state).toBe("candidate");
  });

  it("기억 outdated 는 archived 로 내린다 — 한 단계가 아니다", async () => {
    const result = await api.post<CorrectionResponse>("/corrections", {
      target_ref: { kind: "profile_affinity", id: "a_20" },
      verdict: "outdated",
      child_id: "c1",
    });

    expect((result.target as Affinity).state).toBe("archived");
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

  it("월 조회의 has_event 는 confirmed 만 센다", async () => {
    const month = await api.get<CalendarMonthResponse>("/children/c1/calendar", {
      query: { month: monthOf(new Date()) },
    });

    // 05·06 이 만드는 draft(e_draft_1)는 아직 캘린더에 쓴 것이 아니다.
    for (const day of month.days) {
      if (!day.has_event) continue;
      const detail = await api.get<CalendarDayResponse>(`/children/c1/calendar/${day.date}`);
      expect(detail.events.every((event) => event.status === "confirmed")).toBe(true);
    }
  });

  it("준비물 체크는 다시 읽어도 남아 있다", async () => {
    await api.patch("/event-items/i_1", { is_prepared: true });

    const month = await api.get<CalendarMonthResponse>("/children/c1/calendar", {
      query: { month: monthOf(new Date()) },
    });
    const eventDay = month.days.find((day) => day.has_event);
    expect(eventDay).toBeDefined();

    const detail = await api.get<CalendarDayResponse>(`/children/c1/calendar/${eventDay!.date}`);
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
      policy_version: "2026-09-01",
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
        policy_version: "2026-09-01",
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
      policy_version: "2026-09-01",
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

  it("탈퇴는 화면이 읽은 유예기간을 같이 받는다 — 없으면 막힌다", async () => {
    const caught = await api.post("/auth/withdraw", {}).catch((e) => e);
    expect(isApiError(caught, "validation_failed")).toBe(true);

    const res = await api.post<WithdrawResponse>("/auth/withdraw", {
      acknowledged_grace_days: 30,
    });
    expect(new Date(res.purge_after).getTime()).toBeGreaterThan(Date.now());
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
});

/**
 * 초대 수락. ⚠️ 코드 방식과 응답 모양 둘 다 **계약 확정 전이다** (#96) —
 * 서버가 붙으면 이 표를 실서버에도 그대로 건다.
 */
describe("⑱ 초대 수락 — 아이는 보호자당 한 명", () => {
  it("아이가 없는 계정은 코드로 연결된다", async () => {
    setScenario("consent");
    try {
      const res = await api.post<InviteAcceptResponse>("/invites/MKGRAND1/accept", {});
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
describe("⑲ 가입 — 동의가 빠지면 아무것도 만들어지지 않는다", () => {
  it("계정 동의가 빠지면 signup 이 403 이다", async () => {
    const caught = await api
      .post("/auth/kakao/signup", {
        consent_code: "cc_mock",
        bind: "b",
        nickname: "테스터",
        consents: [{ scope: "service_terms", policy_version: "2026-09-01" }],
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
      consents: [
        { scope: "service_terms", policy_version: "2026-09-01" },
        { scope: "privacy_account", policy_version: "2026-09-01" },
      ],
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
      consents: [
        { scope: "service_terms", policy_version: "2026-09-01" },
        { scope: "privacy_account", policy_version: "2026-09-01" },
        { scope: "location", policy_version: "2026-09-01" },
      ],
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
        consents: [
          { scope: "service_terms", policy_version: "2026-09-01" },
          { scope: "privacy_account", policy_version: "2026-09-01" },
        ],
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
        consents: [{ scope: "child_basic", policy_version: "2026-09-01" }],
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
        consents: [
          { scope: "child_basic", policy_version: "2026-09-01" },
          { scope: "child_health", policy_version: "2026-09-01" },
        ],
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

  it("문서 lane 이 만드는 일정은 draft 다 — 승인 게이트는 여전히 09 에 있다", async () => {
    const runId = await upload();
    const parsed = await parsedOf(runId);

    const saved: PhotoCommitResponse = await commitPhotoRun(runId, {
      lane: "document",
      entries: checked(parsed.entries ?? []),
      attach_to_calendar: true,
    });

    expect(saved.event).not.toBeNull();
    // 🚨 confirmed 로 만들면 승인 게이트가 3곳이 된다 (CLAUDE.md §2).
    expect(saved.event?.status).toBe("draft");
    expect(saved.calendar_date).not.toBeNull();
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
