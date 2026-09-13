import { describe, expect, it, vi } from "vitest";

import { api } from "./client";
import {
  IdempotencyKeyRequiredError,
  idempotentPath,
  newIdempotencyKey,
  requiresIdempotencyKey,
} from "./idempotency";
import { confirmEvent, submitInput } from "./operations";

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
      "/events/e1/cancel",
      "/events/e1/confirm/again",
    ]) {
      expect(requiresIdempotencyKey(path)).toBe(false);
    }
  });
});

describe("키 없는 호출", () => {
  it("요청을 보내지 않고 던진다 — 환경을 가리지 않는다", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");

    await expect(api.post(idempotentPath.confirmEvent("e1"))).rejects.toBeInstanceOf(
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
    const first = await confirmEvent("e_retry", key);
    const second = await confirmEvent("e_retry", key);
    expect(second).toEqual(first);
  });
});
