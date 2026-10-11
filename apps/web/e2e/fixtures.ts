import { test as base, expect, type Page, type Request } from "@playwright/test";

import type { Scenario } from "@/mocks/scenario";

/**
 * 화면 규칙 테스트의 공통 바탕 (#242).
 *
 * 테스트마다 새 브라우저 컨텍스트가 뜬다 (Playwright 기본값). 목 상태는 그 탭의 JS 메모리에만
 * 살아서(MSW 브라우저 모드) 테스트끼리 섞이지 않는다.
 *
 * 🚨 **테스트 중간에 `page.goto` · `reload` 를 하면 목 상태와 대화 스토어가 같이 날아간다.**
 *    "등록한 뒤 다른 화면에서 보인다" 같은 흐름은 화면 안의 링크로 이어 간다.
 *
 * 🚨 **MSW 가 서비스 워커로 응답해서 `page.route` 로 응답을 바꿀 수 없다.** 실패를 만들고 싶으면
 *    목 시나리오를 더한다 (apps/web/CLAUDE.md §8 "테스트 전용 핸들러를 만들지 않는다").
 */

/** 목이 심는 세션. `stores/session.ts` 의 persist 모양과 같아야 한다 (/web-smoke 와 같은 값). */
const MOCK_SESSION = JSON.stringify({
  state: { token: "mock-token-do-not-use-in-production", expiresAt: null, activeChildId: null },
  version: 0,
});

/** 목 아이 id (`mocks/fixtures.ts`). */
export const CHILD = "c1";

type Options = {
  /** 목 시나리오. `mocks/scenario.ts` 의 이름. */
  scenario: Scenario;
  /** 로그인한 채로 시작하는가. */
  signedIn: boolean;
  /**
   * 목이 일부러 네트워크 실패를 내는 시나리오(`send_failed`)에서만 켠다. 꺼져 있으면
   * `/api/v1/` 요청이 하나라도 실패할 때 테스트가 실패한다 — 목을 통과해 실서버로 나간 것이다.
   */
  allowFailedApi: boolean;
};

export const test = base.extend<Options>({
  scenario: ["default", { option: true }],
  signedIn: [true, { option: true }],
  allowFailedApi: [false, { option: true }],

  // 🚨 두 번째 인자를 `use` 로 부르지 않는다 — React 의 `use` 훅으로 읽혀 lint 가 막는다.
  page: async ({ page, scenario, signedIn, allowFailedApi }, provide) => {
    // 🚨 `?scenario=` 대신 저장소에 심는다. 주소로 넘기면 URL 단언마다 쿼리가 끼어든다.
    //    목은 요청마다 이 값을 다시 읽는다 (`currentScenario()`).
    await page.addInitScript(
      ([s, session, signed]) => {
        localStorage.setItem("icatch.mock.scenario", s);
        if (signed && sessionStorage.getItem("icatch.session") === null) {
          sessionStorage.setItem("icatch.session", session);
        }
      },
      [scenario, MOCK_SESSION, signedIn] as const,
    );

    // 개발 전용 떠 있는 버튼(Next 개발 표시기 · Query devtools)이 하단 네비와 보내기 버튼을 덮는다.
    await page.addInitScript(() => {
      document.addEventListener("DOMContentLoaded", () => {
        const style = document.createElement("style");
        style.textContent =
          ".tsqd-parent-container, [id^='tsqd'] { display: none !important; } nextjs-portal { pointer-events: none; }";
        document.head.appendChild(style);
      });
    });

    const problems: string[] = [];
    page.on("pageerror", (error) => problems.push(`미처리 예외: ${error.name}`));
    page.on("console", (message) => {
      if (message.type() !== "error") return;
      const text = message.text();
      // 목이 일부러 4xx · 5xx 를 내는 시나리오가 있다 (409 중복 · 429 한도). 브라우저가 그 응답마다
      // 찍는 줄이고, 아래 "목을 통과한 요청" 검사가 진짜 문제를 따로 잡는다.
      if (text.startsWith("Failed to load resource")) return;
      problems.push(`콘솔 에러: ${text.slice(0, 200)}`);
    });
    page.on("response", (response) => {
      if (!isApi(response.request()) || response.fromServiceWorker()) return;
      problems.push(`목을 통과한 요청: ${response.request().method()} ${response.url()}`);
    });
    page.on("requestfailed", (request) => {
      if (!isApi(request) || allowFailedApi) return;
      // 화면이 스스로 끊은 것이다 — 끝난 run 의 SSE 를 닫거나, 화면을 떠나며 요청을 거둔다.
      // 목을 통과해 실서버(없는 주소)로 나간 요청은 ERR_CONNECTION_REFUSED 로 온다.
      if (request.failure()?.errorText === "net::ERR_ABORTED") return;
      problems.push(`API 요청 실패 (목이 꺼져 있거나 목을 통과): ${request.url()}`);
    });

    await provide(page);

    expect(
      problems,
      "개발 서버가 목을 끈 채로 떠 있으면 여기서 실패한다 — .env.local 의 NEXT_PUBLIC_API_MOCKING=enabled 확인",
    ).toEqual([]);
  },
});

export { expect };

function isApi(request: Request): boolean {
  return new URL(request.url()).pathname.startsWith("/api/v1/");
}

/* ── 요청 기록 ─────────────────────────────────────────────────────────── */

export type ApiCall = {
  method: string;
  /** `/api/v1` 아래 경로 (쿼리 제외). */
  path: string;
  body: unknown;
  idempotencyKey: string | undefined;
};

/**
 * 이 페이지가 보낸 API 요청을 모은다. "누르기 전에는 0건" 같은 단언에 쓴다.
 * 서비스 워커가 응답해도 페이지의 요청 이벤트에는 잡힌다.
 */
export function recordApi(page: Page): ApiCall[] {
  const calls: ApiCall[] = [];
  page.on("request", (request) => {
    if (!isApi(request)) return;
    let body: unknown = null;
    try {
      body = request.postDataJSON();
    } catch {
      body = request.postData();
    }
    calls.push({
      method: request.method(),
      path: new URL(request.url()).pathname.replace(/^\/api\/v1/, ""),
      body,
      idempotencyKey: request.headers()["idempotency-key"],
    });
  });
  return calls;
}

/** 쓰기 요청만 (GET 제외). */
export function writes(calls: ApiCall[], method?: string, path?: RegExp): ApiCall[] {
  return calls.filter(
    (c) =>
      c.method !== "GET" &&
      (method === undefined || c.method === method) &&
      (path === undefined || path.test(c.path)),
  );
}

/* ── 승인 게이트 · 색 ───────────────────────────────────────────────────── */

/**
 * 지금 화면에 보이는 것 중 **승인 게이트의 표식**을 모은다.
 *
 * 게이트는 role 로 구분되지 않는다 — approve 버튼도 primary 와 같은 `<button>` 이다. 그래서
 * 디자인 토큰으로 판별한다: approve 버튼은 `--height-approve` 높이, caution 색은 게이트 2곳 전용
 * (apps/web/CLAUDE.md §5). 값은 `globals.css` 에서 그때 읽는다 — 테스트에 색을 박지 않는다.
 */
export async function gateMarks(page: Page, within?: string): Promise<string[]> {
  return page.evaluate((scope) => {
    const root = scope ? document.querySelector(scope) : document.body;
    if (!root) return [];
    const css = getComputedStyle(document.documentElement);
    const caution = ["--color-caution", "--color-caution-soft", "--color-caution-ink"].map((name) =>
      hexToRgb(css.getPropertyValue(name).trim()),
    );
    const marks: string[] = [];
    for (const el of root.querySelectorAll<HTMLElement>("*")) {
      if (!el.checkVisibility()) continue;
      const style = getComputedStyle(el);
      const label = (el.innerText || el.getAttribute("aria-label") || el.tagName)
        .trim()
        .slice(0, 40);
      if (el.classList.contains("min-h-approve")) marks.push(`approve 버튼: ${label}`);
      const colors = [
        style.backgroundColor,
        style.color,
        style.borderLeftColor,
        style.borderTopColor,
      ];
      if (colors.some((c) => caution.includes(c))) marks.push(`caution 색: ${label}`);
    }
    return marks;

    function hexToRgb(hex: string): string {
      const n = parseInt(hex.replace("#", ""), 16);
      return `rgb(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255})`;
    }
  }, within);
}

/** 승인 게이트 2곳(㉠ 캘린더 쓰기 · ㉡ 건강·알레르기 확정) 밖에서는 표식이 하나도 없어야 한다. */
export async function expectNoGate(page: Page, within?: string): Promise<void> {
  expect(
    await gateMarks(page, within),
    "승인 게이트는 딱 2곳이다 (최상위 CLAUDE.md §2 실행)",
  ).toEqual([]);
}

/** 발달 평가로 읽히는 낱말 (최상위 CLAUDE.md §2 개인정보 · §1 안 만드는 것). */
export const EVALUATIVE = /백분위|또래|평균|정상|빠르|느리|표준|성장곡선|상위|하위|%/;

/** 페이지에 보이는 글자 전체 (접근성 트리와 무관하게 innerText). */
export async function visibleText(page: Page): Promise<string> {
  return page.evaluate(() => document.body.innerText);
}
