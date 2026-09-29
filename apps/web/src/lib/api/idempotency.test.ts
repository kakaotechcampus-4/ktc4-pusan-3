import { describe, expect, it, vi } from "vitest";

import { api } from "./client";
import {
  createIdempotencyKeyHolder,
  IdempotencyKeyRequiredError,
  idempotentPath,
  newIdempotencyKey,
  requiresIdempotencyKey,
} from "./idempotency";
import { submitEventDraft, submitInput } from "./operations";
import type { SubmitEventBody } from "./types";

/** 제출 본문 한 벌. 🚨 일자가 있어야 목이 받는다 (화면이 막는 것과 같은 규칙). */
const DRAFT_BODY: SubmitEventBody = {
  event: {
    title: "지어낸 일정",
    starts_at: "2026-09-18T10:00:00+09:00",
    ends_at: null,
    all_day: false,
    event_type: "episodic",
    category: "activity",
  },
  items: [],
};

/**
 * 클라이언트 쪽 강제.
 *
 * 멘토 리뷰(#29 질문 4): "경고와 강제는 구분하면 좋겠습니다. 현재 클라이언트는 개발 환경에서
 * 경고를 출력하고 요청은 그대로 보내요." → 아래는 **요청이 나가지 않는다**를 확인한다.
 */

describe("되돌릴 수 없는 경로 판정", () => {
  it("표의 5개 경로를 전부 요구한다", () => {
    const paths = Object.values(idempotentPath).map((build) => build("x1"));
    expect(paths).toHaveLength(5);
    for (const path of paths) expect(requiresIdempotencyKey(path)).toBe(true);
  });

  it("이웃한 읽기 경로를 잘못 걸지 않는다", () => {
    // /children/{cid}/inputs 와 한 글자 차이인 경로들. [^/]+ 가 새는지 본다.
    for (const path of [
      "/children/c1/observations",
      "/children/c1/inputs/extra",
      "/children/c1/health-safety/hs_1",
      "/children/c1/events/e1",
      "/children/c1/events/e1/items",
    ]) {
      expect(requiresIdempotencyKey(path)).toBe(false);
    }
  });
});

describe("키 없는 호출", () => {
  it("요청을 보내지 않고 던진다 — 환경을 가리지 않는다", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");

    await expect(api.post(idempotentPath.submitEvent("c1"))).rejects.toBeInstanceOf(
      IdempotencyKeyRequiredError,
    );
    // 🚨 서버의 400 에 기대지 않는다. fetch 자체가 호출되지 않아야 한다.
    expect(fetchSpy).not.toHaveBeenCalled();

    fetchSpy.mockRestore();
  });

  it("GET 은 막지 않는다 — 되돌릴 것이 없다", async () => {
    // 같은 경로라도 읽기는 키가 필요 없다 (계약서 §01 은 POST 만 말한다).
    await expect(api.get(idempotentPath.healthSafety("c1"))).resolves.toBeTruthy();
  });
});

describe("전용 함수", () => {
  it("헤더에 받은 키를 그대로 싣는다", async () => {
    const key = newIdempotencyKey();
    const fetchSpy = vi.spyOn(globalThis, "fetch");

    await submitInput("c1", { text: "지어낸 한 줄", source: "home_input" }, key);

    const [, init] = fetchSpy.mock.calls[0];
    expect(new Headers(init?.headers).get("Idempotency-Key")).toBe(key);
    fetchSpy.mockRestore();
  });

  it("같은 키를 다시 넘기면 같은 응답을 받는다 (재시도)", async () => {
    const key = newIdempotencyKey();
    const first = await submitEventDraft("c1", DRAFT_BODY, key);
    const second = await submitEventDraft("c1", DRAFT_BODY, key);
    expect(second).toEqual(first);
  });
});

/**
 * 키 수명 — 본문이 바뀌면 키도 바뀐다.
 *
 * 멘토 리뷰(#71): "최초 POST 를 서버가 처리했지만 응답만 유실되면 (…) 사용자가 내용을 고쳐
 * 다시 전송하면 기존 키에 다른 본문이 묶여 422 `idempotency_key_reuse` 가 계속 발생하고,
 * 키를 바꾸는 경로에도 진입하지 못합니다."
 */
describe("키 수명 — 본문에 묶인 키", () => {
  it("같은 본문이면 같은 키다 — 그게 재시도다", () => {
    const holder = createIdempotencyKeyHolder();
    expect(holder.current("한 줄 A")).toBe(holder.current("한 줄 A"));
  });

  it("🚨 본문을 고쳐 쓰면 새 키다", () => {
    const holder = createIdempotencyKeyHolder();
    const first = holder.current("한 줄 A");
    expect(holder.current("한 줄 B")).not.toBe(first);
  });

  it("rotate 뒤에는 같은 본문이어도 새 키다 — 다음 동작이다", () => {
    const holder = createIdempotencyKeyHolder();
    const first = holder.current("한 줄 A");
    holder.rotate();
    expect(holder.current("한 줄 A")).not.toBe(first);
  });

  it("본문을 안 넘기는 동작(제출 · 승인)은 예전 그대로다", () => {
    const holder = createIdempotencyKeyHolder();
    expect(holder.current()).toBe(holder.current());
  });
});

describe("응답 유실 → 본문 수정 → 재전송", () => {
  const body = (text: string) => ({ text, source: "home_input" }) as const;

  it("같은 키에 다른 본문을 보내면 422 다 — 고쳐야 하는 이유", async () => {
    const key = newIdempotencyKey();
    // 서버는 처리했고(여기까지는 성공) 화면만 응답을 못 받은 상태를 흉내 낸다.
    await submitInput("c1", body("지어낸 한 줄"), key);

    await expect(submitInput("c1", body("지어낸 다른 한 줄"), key)).rejects.toMatchObject({
      code: "idempotency_key_reuse",
    });
  });

  it("holder 를 거치면 고쳐 쓴 본문이 새 키로 나가 통과한다", async () => {
    const holder = createIdempotencyKeyHolder();

    const first = body("지어낸 한 줄");
    await submitInput("c1", first, holder.current(first.text));

    // 보호자가 한 줄을 고쳐서 다시 보낸다. rotate 를 부른 적은 없다 — 부를 자리가 없는 경로다.
    const edited = body("지어낸 다른 한 줄");
    await expect(submitInput("c1", edited, holder.current(edited.text))).resolves.toHaveProperty(
      "run_id",
    );
  });
});
