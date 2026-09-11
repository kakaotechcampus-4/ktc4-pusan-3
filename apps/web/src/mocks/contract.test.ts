import { describe, expect, it } from "vitest";

import { API_BASE_URL } from "@/lib/env";
import { ApiError, isApiError } from "@/lib/api/errors";
import { idempotentPath, newIdempotencyKey } from "@/lib/api/idempotency";
import { addHealthSafety, confirmEvent, submitOnboarding } from "@/lib/api/operations";
import { streamRunEvents } from "@/lib/api/sse";
import { api } from "@/lib/api/client";

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
