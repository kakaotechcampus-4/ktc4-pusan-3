import { http, HttpResponse } from "msw";
import { afterEach, describe, expect, it, vi } from "vitest";

import { setAuthToken } from "@/lib/api/client";
import { url } from "@/mocks/handlers/helpers";
import { server } from "@/mocks/server";
import { platformSummary, reportRenderError, sendClientError } from "@/lib/report-render-error";

/**
 * 화면 오류를 서버로 보낸다 (멘토 #267 2번 · #166). 그 전엔 console.error 뿐이라 보호자 폰의 콘솔에만
 * 남았다.
 *
 * 🚨 보내는 건 예외 종류 · digest · 화면 경로 · 기기 요약뿐이다. 예외 메시지는 보내지 않는다 — 서버
 *    에러 봉투의 문구와 보호자가 방금 친 한 줄(raw_text)이 실릴 수 있다 (루트 CLAUDE.md §2).
 * 🚨 로그인하지 않았으면 보내지 않는다 — 서버가 로그인한 보호자만 받는다 (POST /client-errors).
 */

const ANDROID =
  "Mozilla/5.0 (Linux; Android 14; SM-S921N) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Mobile Safari/537.36";
const IPHONE =
  "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1";
const MAC =
  "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36";
const WINDOWS =
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36";

describe("platformSummary — 긴 식별 문자열에서 운영체제 · 주요 버전 · 앱/브라우저만", () => {
  it.each([
    [ANDROID, true, "android 14 app"],
    [ANDROID, false, "android 14 browser"],
    [IPHONE, true, "ios 17 app"],
    [MAC, false, "mac 10 browser"],
    [WINDOWS, false, "windows 10 browser"],
    ["Something/1.0", false, "other browser"],
  ])("%s", (ua, isApp, expected) => {
    expect(platformSummary(ua, isApp)).toBe(expected);
  });

  it("서버가 받는 모양(소문자 · 숫자 · 공백 · 40자 이내)을 벗어나지 않는다 — 기종 · 빌드 번호는 없다", () => {
    const summary = platformSummary(ANDROID, true);
    expect(summary).toMatch(/^[a-z0-9 ._-]{1,40}$/);
    expect(summary).not.toContain("SM-S921N");
  });
});

describe("sendClientError", () => {
  afterEach(() => setAuthToken(null));

  it("로그인했으면 POST /client-errors 에 JSON 으로 보내고, 토큰을 붙인다", async () => {
    let body: unknown = null;
    let authorization: string | null = null;
    server.use(
      http.post(url("/client-errors"), async ({ request }) => {
        body = await request.json();
        authorization = request.headers.get("authorization");
        return new HttpResponse(null, { status: 204 });
      }),
    );
    setAuthToken("test-token");

    await sendClientError({
      name: "TypeError",
      digest: "123",
      path: "/records",
      platform: "android 14 app",
    });

    expect(body).toEqual({
      name: "TypeError",
      digest: "123",
      path: "/records",
      platform: "android 14 app",
    });
    expect(authorization).toBe("Bearer test-token");
  });

  it("🚨 로그인하지 않았으면 보내지 않는다", async () => {
    let called = false;
    server.use(
      http.post(url("/client-errors"), () => {
        called = true;
        return new HttpResponse(null, { status: 204 });
      }),
    );

    await sendClientError({
      name: "TypeError",
      digest: null,
      path: "/",
      platform: "other browser",
    });

    expect(called).toBe(false);
  });

  it("서버가 실패해도 화면 쪽으로 예외가 올라오지 않는다 — 오류 화면에서 또 터지면 안 된다", async () => {
    server.use(http.post(url("/client-errors"), () => HttpResponse.error()));
    setAuthToken("test-token");

    await expect(
      sendClientError({ name: "TypeError", digest: null, path: "/", platform: "other browser" }),
    ).resolves.toBeUndefined();
  });
});

describe("reportRenderError", () => {
  afterEach(() => {
    setAuthToken(null);
    vi.restoreAllMocks();
  });

  const env = {
    pathname: "/children/0b6f5c1e-0000-4000-8000-000000000001/records",
    userAgent: ANDROID,
    isApp: true,
  };

  it("🚨 예외 메시지는 콘솔에도 서버에도 가지 않는다 — 종류 · 경로 · 기기만", async () => {
    let body: string | null = null;
    server.use(
      http.post(url("/client-errors"), async ({ request }) => {
        body = await request.text();
        return new HttpResponse(null, { status: 204 });
      }),
    );
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
    setAuthToken("test-token");

    reportRenderError(new TypeError("SECRET-보호자가-친-문장"), env);

    await vi.waitFor(() => expect(body).not.toBeNull());
    expect(body).not.toContain("SECRET");
    expect(JSON.parse(body ?? "{}")).toEqual({
      name: "TypeError",
      digest: null,
      path: "/children/0b6f5c1e-0000-4000-8000-000000000001/records",
      platform: "android 14 app",
    });
    expect(JSON.stringify(consoleError.mock.calls)).not.toContain("SECRET");
  });

  it("화면 정보를 모르면(서버 렌더 등) 콘솔에만 남기고 보내지 않는다", () => {
    let called = false;
    server.use(
      http.post(url("/client-errors"), () => {
        called = true;
        return new HttpResponse(null, { status: 204 });
      }),
    );
    vi.spyOn(console, "error").mockImplementation(() => {});
    setAuthToken("test-token");

    reportRenderError(new Error("x"), null);

    expect(called).toBe(false);
  });
});
