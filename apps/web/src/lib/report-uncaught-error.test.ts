import { http, HttpResponse } from "msw";
import { afterEach, describe, expect, it, vi } from "vitest";

import { setAuthToken } from "@/lib/api/client";
import { ApiError, NetworkError } from "@/lib/api/errors";
import type { ClientErrorReport } from "@/lib/report-render-error";
import { installUncaughtErrorReporting, isNoise } from "@/lib/report-uncaught-error";
import { url } from "@/mocks/handlers/helpers";
import { server } from "@/mocks/server";

/**
 * 렌더 밖 예외도 서버로 (#732). 바운더리에 닿지 않는 이벤트 핸들러 · 타이머 · 처리 안 된 rejection.
 *
 * 🚨 보내는 모양은 렌더 오류와 같다 — 이름 · digest · 경로 · 기기 요약. 메시지는 없다 (루트 CLAUDE.md §2).
 * 🚨 잡음은 보내지 않는다 · 같은 오류는 한 번만 · 전체 상한.
 *
 * 테스트는 node 에서 돈다 — `window` 대신 `EventTarget` 에 브라우저와 같은 이름의 이벤트를 던진다.
 */

const ORIGIN = "https://icatch.ai.kr";
const env = { pathname: "/child/abc/home", userAgent: "Something/1.0", isApp: false };

function setup(limit?: number) {
  const target = new EventTarget();
  const sent: ClientErrorReport[] = [];
  const stop = installUncaughtErrorReporting({
    target,
    origin: ORIGIN,
    environment: () => env,
    send: async (report) => {
      sent.push(report);
    },
    limit,
  });
  const throwError = (error: unknown, filename = `${ORIGIN}/_next/static/chunks/app.js`) =>
    target.dispatchEvent(Object.assign(new Event("error"), { error, filename }));
  const reject = (reason: unknown) =>
    target.dispatchEvent(Object.assign(new Event("unhandledrejection"), { reason }));
  return { sent, stop, throwError, reject };
}

describe("installUncaughtErrorReporting", () => {
  it("이벤트 핸들러의 예외를 보낸다 — 이름 · 경로 · 기기만, 메시지는 없다", () => {
    const { sent, throwError } = setup();

    throwError(new TypeError("SECRET-보호자가-친-문장"));

    expect(sent).toEqual([
      { name: "TypeError", digest: null, path: "/child/abc/home", platform: "other browser" },
    ]);
    expect(JSON.stringify(sent)).not.toContain("SECRET");
  });

  it("처리 안 된 rejection 을 보낸다", () => {
    const { sent, reject } = setup();

    reject(new RangeError("x"));

    expect(sent.map((r) => r.name)).toEqual(["RangeError"]);
  });

  it('Error 가 아닌 값을 던져도 종류만 보낸다 — `throw "문자열"` 도 버그다', () => {
    const { sent, throwError, reject } = setup();

    throwError("SECRET");
    reject(undefined);

    expect(sent.map((r) => r.name)).toEqual(["string", "undefined"]);
    expect(JSON.stringify(sent)).not.toContain("SECRET");
  });

  it("🚨 예외 객체가 없는 error 이벤트는 보내지 않는다 — 다른 출처의 Script error. · ResizeObserver", () => {
    const { sent, throwError } = setup();

    throwError(null);
    throwError(undefined);

    expect(sent).toEqual([]);
  });

  it("🚨 우리 출처가 아닌 파일(브라우저 확장)에서 난 예외는 보내지 않는다", () => {
    const { sent, throwError } = setup();

    throwError(new TypeError("x"), "chrome-extension://abc/content.js");
    throwError(new TypeError("x"), `${ORIGIN}.evil.example/x.js`);
    expect(sent).toEqual([]);

    // 파일 이름이 비어 있으면(인라인 · eval) 걸러낼 근거가 없어 보낸다
    throwError(new TypeError("x"), "");
    expect(sent).toHaveLength(1);
  });

  it("🚨 잡음은 보내지 않는다 — ApiError · NetworkError · AbortError", () => {
    const { sent, reject } = setup();

    reject(new ApiError(500, "internal_error", "x"));
    reject(new NetworkError(new TypeError("Failed to fetch")));
    reject(Object.assign(new Error("x"), { name: "AbortError" }));

    expect(sent).toEqual([]);
  });

  it("🚨 같은 오류(이름 + 경로)는 한 번만 — 누를 때마다 또 나도 채널을 도배하지 않는다", () => {
    const { sent, throwError, reject } = setup();

    throwError(new TypeError("a"));
    throwError(new TypeError("b"));
    reject(new TypeError("c"));

    expect(sent).toHaveLength(1);
  });

  it("🚨 서로 다른 오류도 상한까지만 보낸다", () => {
    const { sent, throwError } = setup(2);

    throwError(new TypeError("x"));
    throwError(new RangeError("x"));
    throwError(new SyntaxError("x"));

    expect(sent.map((r) => r.name)).toEqual(["TypeError", "RangeError"]);
  });

  it("그만 들으면 더 보내지 않는다 — Providers 가 내려갈 때", () => {
    const { sent, stop, throwError } = setup();

    stop();
    throwError(new TypeError("x"));

    expect(sent).toEqual([]);
  });

  it("화면 정보를 모르면 보내지 않는다", () => {
    const target = new EventTarget();
    const send = vi.fn(async () => {});
    installUncaughtErrorReporting({ target, origin: ORIGIN, environment: () => null, send });

    target.dispatchEvent(
      Object.assign(new Event("unhandledrejection"), { reason: new Error("x") }),
    );

    expect(send).not.toHaveBeenCalled();
  });

  it("`window` 가 없으면(서버) 아무것도 걸지 않는다", () => {
    expect(typeof window).toBe("undefined");
    expect(() => installUncaughtErrorReporting()()).not.toThrow();
  });
});

describe("기본 보내기 — POST /client-errors", () => {
  afterEach(() => setAuthToken(null));

  it("로그인했으면 렌더 오류와 같은 창구로 간다", async () => {
    let body: unknown = null;
    server.use(
      http.post(url("/client-errors"), async ({ request }) => {
        body = await request.json();
        return new HttpResponse(null, { status: 204 });
      }),
    );
    setAuthToken("test-token");
    const target = new EventTarget();
    installUncaughtErrorReporting({ target, origin: ORIGIN, environment: () => env });

    target.dispatchEvent(
      Object.assign(new Event("unhandledrejection"), { reason: new TypeError("x") }),
    );

    await vi.waitFor(() => expect(body).not.toBeNull());
    expect(body).toEqual({
      name: "TypeError",
      digest: null,
      path: "/child/abc/home",
      platform: "other browser",
    });
  });
});

describe("isNoise", () => {
  it.each([
    [new ApiError(404, "not_found", "x"), true],
    [new NetworkError(null), true],
    [Object.assign(new Error("x"), { name: "AbortError" }), true],
    [new TypeError("x"), false],
    [null, false],
    ["x", false],
  ])("%s → %s", (error, expected) => {
    expect(isNoise(error)).toBe(expected);
  });
});
