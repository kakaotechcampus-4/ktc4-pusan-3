import { delay, http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import {
  buildWebAlert,
  createWebAlerter,
  shouldSendWebAlert,
  webAlertEnv,
  type RequestErrorContext,
} from "@/lib/server-error-alert";
import { server } from "@/mocks/server";

/**
 * Next 서버에서 난 오류를 Discord 로 보낸다 (멘토 #267 2번 · #166). `src/instrumentation.ts` 의
 * `onRequestError` 가 부른다. Next 서버에는 보호자 토큰이 없어서 api 의 /client-errors 를 못 쓰고
 * 웹훅으로 직접 보낸다 — 서버 코드라 URL 이 브라우저 번들에 들어가지 않는다.
 *
 * 🚨 예외 메시지 · 쿼리 문자열은 보내지 않는다 (루트 CLAUDE.md §2). 종류 이름 · digest · 경로 · 라우트.
 * 🚨 분당 상한 — 한 페이지가 매 요청 터지면 Discord 를 도배한다.
 */

const WEBHOOK = "https://discord.com/api/webhooks/1/SECRET-TOKEN";
const REQUEST = { path: "/children/abc/records?tab=all", method: "GET", headers: {} };
const CONTEXT: RequestErrorContext = {
  routerKind: "App Router",
  routePath: "/children/[cid]/records",
  routeType: "render",
  revalidateReason: undefined,
};

describe("buildWebAlert — 종류 · digest · 경로 · 라우트만, 메시지는 없다", () => {
  it("Error 면 생성자 이름과 digest, 요청 경로는 쿼리를 뗀다", () => {
    const error = Object.assign(new TypeError("SECRET-보호자가-친-문장"), { digest: "abc123" });

    const alert = buildWebAlert(error, REQUEST, CONTEXT, { env: "prod", now: new Date(0) });

    expect(alert.username).toBe("web-alert");
    expect(alert.allowed_mentions).toEqual({ parse: [] });
    const [first, second, third] = alert.content.split("\n");
    expect(first).toBe(
      "🟡 [prod] ERROR web render /children/[cid]/records — TypeError digest=abc123 path=/children/abc/records",
    );
    expect(second).toMatch(/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}[+-]\d{4}$/);
    expect(third).toBe('→ journalctl CONTAINER_NAME=ktc4-web --since "10 min ago"');
    expect(alert.content).not.toContain("SECRET");
    expect(alert.content).not.toContain("tab=all");
  });

  it("Error 가 아닌 것이 던져져도 터지지 않는다 — 종류는 typeof, digest 는 -", () => {
    const alert = buildWebAlert("boom", REQUEST, CONTEXT, { env: "prod", now: new Date(0) });

    expect(alert.content).toContain("— string digest=- path=");
  });
});

describe("createWebAlerter — 보내기와 분당 상한", () => {
  it("웹훅에 JSON 으로 POST 하고 저장까지 기다린다(wait=true)", async () => {
    let body: unknown = null;
    let wait: string | null = null;
    server.use(
      http.post(WEBHOOK, async ({ request }) => {
        body = await request.json();
        wait = new URL(request.url).searchParams.get("wait");
        return HttpResponse.json({ id: "1" });
      }),
    );
    const send = createWebAlerter({ url: WEBHOOK, perMinute: 10, now: () => 0 });

    await send(buildWebAlert(new Error("x"), REQUEST, CONTEXT, { env: "prod", now: new Date(0) }));

    expect((body as { username: string }).username).toBe("web-alert");
    expect(wait).toBe("true");
  });

  it("분당 상한을 넘기면 보내지 않고, 다음 분에 다시 보낸다", async () => {
    let posts = 0;
    server.use(
      http.post(WEBHOOK, () => {
        posts += 1;
        return HttpResponse.json({ id: "1" });
      }),
    );
    let clock = 0;
    const send = createWebAlerter({ url: WEBHOOK, perMinute: 3, now: () => clock });
    const alert = buildWebAlert(new Error("x"), REQUEST, CONTEXT, {
      env: "prod",
      now: new Date(0),
    });

    for (let i = 0; i < 5; i += 1) await send(alert);
    expect(posts).toBe(3);

    clock = 61_000;
    await send(alert);
    expect(posts).toBe(4);
  });

  it("🚨 웹훅이 응답하지 않아도 기한 안에 돌아온다 — Next 는 onRequestError 를 기다린 뒤 500 을 낸다", async () => {
    server.use(
      http.post(WEBHOOK, async () => {
        await delay("infinite");
        return HttpResponse.json({ id: "1" });
      }),
    );
    const send = createWebAlerter({ url: WEBHOOK, perMinute: 10, now: () => 0, timeoutMs: 50 });

    const started = Date.now();
    await send(buildWebAlert(new Error("x"), REQUEST, CONTEXT, { env: "prod", now: new Date(0) }));

    expect(Date.now() - started).toBeLessThan(1000);
  });

  it("웹훅이 실패해도 예외가 올라오지 않는다 — 요청 처리 쪽에 영향이 없어야 한다", async () => {
    server.use(http.post(WEBHOOK, () => HttpResponse.error()));
    const send = createWebAlerter({ url: WEBHOOK, perMinute: 10, now: () => 0 });

    await expect(
      send(buildWebAlert(new Error("x"), REQUEST, CONTEXT, { env: "prod", now: new Date(0) })),
    ).resolves.toBeUndefined();
  });
});

describe("webAlertEnv — 꼬리표는 compose 가 넘기는 APP_ENV", () => {
  it.each([
    ["dev", "dev"], // 시험 서버는 deploy/docker/.env 에 APP_ENV=dev 한 줄만 다르다
    ["prod", "prod"],
    [undefined, "prod"], // 넘기지 않으면 진짜 서버로 본다 — 알림은 production 빌드에서만 나간다
    ["  ", "prod"],
  ])("APP_ENV=%s → %s", (value, expected) => {
    expect(webAlertEnv(value)).toBe(expected);
  });
});

describe("shouldSendWebAlert — 언제 보내나", () => {
  it.each([
    ["production", WEBHOOK, true],
    ["production", "", false],
    ["production", undefined, false],
    ["development", WEBHOOK, false], // 🚨 노트북의 오류가 팀 채널로 가지 않게 (api 의 local 규칙과 같다)
    ["test", WEBHOOK, false],
  ])("NODE_ENV=%s url=%s → %s", (nodeEnv, url, expected) => {
    expect(shouldSendWebAlert({ nodeEnv, url })).toBe(expected);
  });
});
