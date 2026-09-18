import { describe, expect, it } from "vitest";

import { createSerialQueue } from "./serial-queue";

/**
 * 멘토 리뷰(#71): "체크한 뒤 첫 PATCH 가 끝나기 전에 해제하면 같은 준비물에 `true`, `false`
 * 요청이 동시에 나갑니다. 해제 요청이 먼저 처리되고 이전 체크 요청이 나중에 처리되면 최종 서버
 * 값은 `true` 가 되고, `onSettled` 의 재조회도 그 값을 받아 마지막 사용자 선택을 되돌립니다."
 *
 * 그래서 아래가 거는 것은 하나다 — **마지막으로 누른 값이 서버에 마지막으로 도착한다.**
 */

/** 서버 흉내. 도착 순서대로 `stored` 를 덮는다 — 실제 PATCH 가 하는 일이 이것이다. */
function fakeServer() {
  const arrived: boolean[] = [];
  let stored: boolean | null = null;

  return {
    get stored() {
      return stored;
    },
    get arrived() {
      return arrived;
    },
    /** `delay` 만큼 걸리는 요청. 응답 역전을 만들려고 건마다 다르게 준다. */
    patch(value: boolean, delay: number): Promise<void> {
      return new Promise((resolve) =>
        setTimeout(() => {
          arrived.push(value);
          stored = value;
          resolve();
        }, delay),
      );
    },
  };
}

describe("같은 항목의 연속 변경", () => {
  it("🚨 줄을 세우지 않으면 마지막 선택이 뒤집힌다 — 고치기 전 동작", async () => {
    const server = fakeServer();

    // 체크(느림) 직후 해제(빠름). 둘이 동시에 나가면 느린 쪽이 나중에 도착한다.
    await Promise.all([server.patch(true, 30), server.patch(false, 1)]);

    expect(server.arrived).toEqual([false, true]);
    expect(server.stored).toBe(true); // 사용자가 마지막에 고른 값은 false 였다
  });

  it("줄을 세우면 마지막으로 누른 값이 마지막에 도착한다", async () => {
    const server = fakeServer();
    const queue = createSerialQueue();

    await Promise.all([
      queue.run(() => server.patch(true, 30)),
      queue.run(() => server.patch(false, 1)),
    ]);

    expect(server.arrived).toEqual([true, false]);
    expect(server.stored).toBe(false);
  });

  it("앞 요청이 끝난 뒤에 다음이 시작된다", async () => {
    const queue = createSerialQueue();
    const log: string[] = [];

    const slow = queue.run(async () => {
      log.push("1 시작");
      await new Promise((resolve) => setTimeout(resolve, 20));
      log.push("1 끝");
    });
    const fast = queue.run(async () => {
      log.push("2 시작");
    });

    await Promise.all([slow, fast]);
    expect(log).toEqual(["1 시작", "1 끝", "2 시작"]);
  });
});

describe("실패해도 줄은 흐른다", () => {
  it("앞 요청이 터져도 다음 요청은 나간다", async () => {
    const queue = createSerialQueue();
    const ran: string[] = [];

    const failing = queue.run(async () => {
      ran.push("1");
      throw new Error("저장 실패");
    });
    const next = queue.run(async () => {
      ran.push("2");
      return "ok";
    });

    // 🚨 실패는 호출자에게 그대로 전달된다 — 삼키면 화면이 되돌리기·토스트를 못 한다.
    await expect(failing).rejects.toThrow("저장 실패");
    await expect(next).resolves.toBe("ok");
    expect(ran).toEqual(["1", "2"]);
  });
});

describe("pending — 마지막 하나만 재조회하려고 센다", () => {
  it("줄에 남은 요청 수를 센다", async () => {
    const queue = createSerialQueue();
    expect(queue.pending).toBe(0);

    const first = queue.run(() => new Promise<void>((resolve) => setTimeout(resolve, 5)));
    const second = queue.run(async () => undefined);
    expect(queue.pending).toBe(2);

    await first;
    // 🚨 앞 요청이 끝난 시점에는 아직 뒤 요청이 남아 있다 — 여기서 재조회하면 낡은 값을 받는다.
    expect(queue.pending).toBe(1);

    await second;
    expect(queue.pending).toBe(0);
  });

  it("실패한 요청도 줄에서 빠진다", async () => {
    const queue = createSerialQueue();
    await expect(
      queue.run(async () => {
        throw new Error("저장 실패");
      }),
    ).rejects.toThrow();
    expect(queue.pending).toBe(0);
  });
});
