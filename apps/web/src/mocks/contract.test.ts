import { describe, expect, it } from "vitest";

import { API_BASE_URL } from "@/lib/env";
import { ApiError, isApiError } from "@/lib/api/errors";
import { idempotentPath, newIdempotencyKey } from "@/lib/api/idempotency";
import { addHealthSafety, confirmEvent, submitOnboarding } from "@/lib/api/operations";
import { streamRunEvents } from "@/lib/api/sse";
import { api } from "@/lib/api/client";
import type {
  Affinity,
  AffinitiesResponse,
  CalendarDayResponse,
  CalendarMonthResponse,
  CorrectionResponse,
  Observation,
  ObservationsResponse,
  SuggestionFeedbackResponse,
} from "@/lib/api/types";

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

  it("프로필 once_only 는 confirmed 를 candidate 로 한 단계만 내린다", async () => {
    const result = await api.post<CorrectionResponse>("/corrections", {
      target_ref: { kind: "profile_affinity", id: "a_12" },
      verdict: "once_only",
      child_id: "c1",
    });

    expect((result.target as Affinity).state).toBe("candidate");
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

/** 목이 만든 달을 그대로 물어보기 위한 키. 표시가 아니라 쿼리 파라미터다. */
function monthOf(date: Date): string {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}`;
}
