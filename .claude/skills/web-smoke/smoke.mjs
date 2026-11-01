#!/usr/bin/env node
/**
 * apps/web 의 화면이 **실제로 뜨는지** 훑는다.
 *
 * 단위 테스트가 아니다. 라우트를 진짜 브라우저로 열어서
 *   ① 최종 경로가 예상과 같은가 (리다이렉트가 설계인 화면이 있다)
 *   ② 콘솔 에러 · 미처리 예외 · Next 에러 오버레이가 없는가
 *   ③ 그 화면이어야만 나오는 문구가 실제로 보이는가
 *   ④ 목 서버를 통과해 실서버로 새 나간 요청이 없는가
 * 를 본다.
 *
 * ④ 가 이 스크립트를 만든 이유다 — startMocks 가 워커보다 먼저 화면을 그려서
 * 첫 요청이 실서버로 나갔고, 로그인 버튼이 영구 비활성이 됐다.
 * 타입 체크·lint·빌드가 전부 통과하는데도 화면이 죽어 있던 종류의 버그다.
 *
 * 쓰는 법: node .claude/skills/web-smoke/smoke.mjs [--headed] [--keep-server]
 */

import { execFileSync, spawn } from "node:child_process";
import { existsSync, mkdirSync, readFileSync, readdirSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const SKILL_DIR = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(SKILL_DIR, "../../..");
const WEB_DIR = join(REPO_ROOT, "apps/web");
const OUT_DIR = join(SKILL_DIR, "out");
const CACHE_DIR = join(SKILL_DIR, ".cache");

const PORT = Number(process.env.SMOKE_PORT ?? 3000);
const BASE = `http://localhost:${PORT}`;
const HEADED = process.argv.includes("--headed");
const KEEP_SERVER = process.argv.includes("--keep-server");

/**
 * 로그인한 채로 여는 방법. 토큰은 메모리에만 있어서 심을 수 없고, 화면은 켤 때 refresh 로 받는다.
 * 그래서 목의 refresh 쿠키 표식을 심는다 (apps/web/src/mocks/handlers/auth.ts 의 MOCK_REFRESH_KEY · #465).
 */
const MOCK_REFRESH_KEY = "icatch.mock.refresh";

/**
 * 무시하는 콘솔 잡음. 🚨 여기에 뭔가 추가할 때는 "왜 무시해도 되는가" 를 같이 적는다 —
 * 이 목록이 길어지면 스크립트가 아무것도 안 잡는 상태로 조용히 썩는다.
 */
const IGNORED_CONSOLE = [
  // React DevTools 설치 권유. 개발 빌드에서 항상 뜬다.
  "Download the React DevTools",
  // Next 개발 서버의 HMR 연결 로그.
  "[Fast Refresh]",
  "[HMR]",
];

/* ── 의존성 ────────────────────────────────────────────────────────────── */

/**
 * playwright-core 를 찾는다. 없으면 스킬 캐시에 받는다 (저장소에는 안 들어간다).
 *
 * 🚨 apps/web 의 `@playwright/test`(화면 규칙 테스트 · #242)가 데려온 것을 먼저 쓴다. pnpm 은
 *    playwright-core 를 apps/web 바로 아래에 두지 않아서, 그 패키지 자리에서 찾아야 한다.
 *    버전이 하나여야 크로미움도 한 벌만 받는다.
 */
function loadPlaywright() {
  const require = createRequire(import.meta.url);
  const candidates = [CACHE_DIR];
  try {
    candidates.unshift(dirname(require.resolve("@playwright/test/package.json", { paths: [WEB_DIR] })));
  } catch {
    /* apps/web 에 설치 전 — 스킬 캐시로 */
  }
  for (const base of candidates) {
    try {
      return require(require.resolve("playwright-core", { paths: [base] }));
    } catch {
      /* 다음 후보 */
    }
  }

  console.log("· playwright-core 가 없다. 스킬 캐시에 받는다 (한 번만)…");
  mkdirSync(CACHE_DIR, { recursive: true });
  execFileSync("npm", ["i", "playwright-core@1.63.0", "--no-audit", "--no-fund", "--silent"], {
    cwd: CACHE_DIR,
    stdio: "inherit",
  });
  return require(require.resolve("playwright-core", { paths: [CACHE_DIR] }));
}

/**
 * 크로미움 실행 파일. playwright-core 는 브라우저를 받지 않으므로 이미 있는 것을 쓴다.
 * 없으면 받는 명령을 알려주고 멈춘다 — 몰래 수백 MB 를 받지 않는다.
 */
function findChromium() {
  if (process.env.SMOKE_CHROME) return process.env.SMOKE_CHROME;

  const home = process.env.HOME ?? "";
  const pwCache =
    process.platform === "darwin"
      ? join(home, "Library/Caches/ms-playwright")
      : join(home, ".cache/ms-playwright");

  if (existsSync(pwCache)) {
    for (const entry of readdirSync(pwCache).filter((d) => d.startsWith("chromium-")).sort().reverse()) {
      for (const rel of [
        "chrome-linux64/chrome",
        "chrome-linux/chrome",
        "chrome-mac/Chromium.app/Contents/MacOS/Chromium",
      ]) {
        const candidate = join(pwCache, entry, rel);
        if (existsSync(candidate)) return candidate;
      }
    }
  }

  for (const candidate of [
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  ]) {
    if (existsSync(candidate)) return candidate;
  }

  console.error(
    "크로미움을 못 찾았다. 아래 중 하나로 준비할 것:\n" +
      "  pnpm --dir apps/web exec playwright install chromium     (권장 · 한 번만)\n" +
      "  SMOKE_CHROME=/path/to/chrome 로 직접 지정",
  );
  process.exit(2);
}

/* ── 개발 서버 ─────────────────────────────────────────────────────────── */

async function alive() {
  try {
    await fetch(BASE, { signal: AbortSignal.timeout(1500) });
    return true;
  } catch {
    return false;
  }
}

/**
 * 이미 떠 있으면 그대로 쓴다 (개발 중에 돌리는 경우).
 * 🚨 목 플래그는 확인만 하고 켜 주지 않는다 — 남의 서버 설정을 이 스크립트가 바꾸지 않는다.
 */
async function ensureServer() {
  if (await alive()) {
    console.log(`· 이미 떠 있는 서버를 쓴다 (${BASE})`);
    return null;
  }

  const envPath = join(WEB_DIR, ".env.local");
  if (!existsSync(envPath)) {
    console.error(`apps/web/.env.local 이 없다. cp .env.example .env.local 먼저 할 것.`);
    process.exit(2);
  }
  if (!/NEXT_PUBLIC_API_MOCKING\s*=\s*enabled/.test(readFileSync(envPath, "utf8"))) {
    console.error(
      "apps/web/.env.local 에 NEXT_PUBLIC_API_MOCKING=enabled 가 없다.\n" +
        "백엔드 없이 화면을 확인하는 스킬이라 목이 필요하다.",
    );
    process.exit(2);
  }

  console.log("· 개발 서버를 띄운다…");
  const proc = spawn("pnpm", ["dev", "--port", String(PORT)], {
    cwd: WEB_DIR,
    stdio: "ignore",
    detached: true,
  });

  const deadline = Date.now() + 90_000;
  while (Date.now() < deadline) {
    if (await alive()) return proc;
    await new Promise((r) => setTimeout(r, 500));
  }
  console.error("개발 서버가 90초 안에 안 떴다.");
  try {
    process.kill(-proc.pid);
  } catch {
    /* 이미 죽음 */
  }
  process.exit(2);
}

/* ── 한 라우트 확인 ────────────────────────────────────────────────────── */

async function checkRoute(browser, route) {
  // 🚨 라우트마다 새 컨텍스트를 쓴다. 앞 화면이 남긴 세션·목 시나리오가 다음 화면에
  //    새지 않고, 심는 값을 navigation 없이 addInitScript 로 넣을 수 있다.
  //    (세션을 심으려고 다른 화면을 먼저 열면, 그 화면이 시작한 요청이 우리 navigation 에
  //     끊겨서 "API 요청 실패" 로 잡힌다 — 실제로 그렇게 오탐이 났다.)
  const ctx = await browser.newContext({
    viewport: { width: 390, height: 844 },
    deviceScaleFactor: 2,
  });
  if (route.auth) {
    await ctx.addInitScript((key) => localStorage.setItem(key, "1"), MOCK_REFRESH_KEY);
    // 화면 투어(#632)를 다 본 채로 연다 — 처음 들어온 화면마다 덮개가 뜨면 스크린샷이 타이밍에 따라 갈린다.
    // 목 보호자 id 는 p1, 키 모양은 apps/web/src/lib/tour/types.ts 의 seenStorageKey · TOUR_KEYS 와 같다.
    await ctx.addInitScript(() =>
      localStorage.setItem(
        "icatch.tour.seen.p1",
        JSON.stringify(["home-empty", "home", "chat", "suggestions", "memories", "calendar", "profile", "growth", "settings"]),
      ),
    );
  }
  // seed — 그 화면에 들어가려면 있어야 하는 sessionStorage 값 (예: 가입 대기표).
  if (route.seed) {
    await ctx.addInitScript((pairs) => {
      for (const [k, v] of Object.entries(pairs)) sessionStorage.setItem(k, v);
    }, route.seed);
  }

  const page = await ctx.newPage();
  const problems = [];
  const warnings = [];

  page.on("pageerror", (e) => problems.push(`미처리 예외: ${e.message}`));
  page.on("console", (m) => {
    if (m.type() !== "error") return;
    const text = m.text();
    if (IGNORED_CONSOLE.some((skip) => text.includes(skip))) return;

    // 부수 리소스(파비콘·이미지·폰트)가 404 인 것은 화면이 깨진 것과 다르다.
    // 놓치면 안 되니 지우지는 않고, 실패로 세지 않는다.
    if (text.includes("Failed to load resource")) {
      const url = m.location()?.url ?? "";
      // 화면이 켤 때마다 refresh 로 세션을 확인한다 (#465). 로그인 전이면 401 이 정상이다.
      if (url.includes("/api/v1/auth/refresh")) return;
      // 문서 자체의 4xx 는 위에서 기대 상태와 맞춰 봤다 (404 화면은 404 가 정상).
      // 여기서 또 경고로 세면 그 화면은 매번 경고를 달고 통과한다.
      if (url.replace(BASE, "") === route.path) return;
      if (!url.includes("/api/v1/")) {
        warnings.push(`부수 리소스 로드 실패: ${url.replace(BASE, "") || text.slice(0, 80)}`);
        return;
      }
    }
    problems.push(`콘솔 에러: ${text.slice(0, 160)}`);
  });
  // 🚨 목을 통과해 실서버로 새 나간 요청. 여기가 비어야 화면이 목 위에서 돈다.
  page.on("requestfailed", (r) => {
    if (r.url().includes("/api/v1/")) problems.push(`API 요청 실패(목을 통과): ${r.url()}`);
  });

  try {
    const res = await page.goto(BASE + route.path, { waitUntil: "domcontentloaded" });
    const status = res?.status() ?? 0;
    // 🚨 404 화면은 **404 로 뜨는 것이 정상**이다 (200 으로 주면 검색엔진·웹뷰가 정상 화면으로 본다).
    //    그래서 기대 상태를 라우트가 정할 수 있게 뒀다. 안 적으면 200 대다.
    const expectStatus = route.status ?? 200;
    if (status !== expectStatus && !(expectStatus === 200 && status < 400)) {
      problems.push(`HTTP ${status} (기대 ${expectStatus})`);
    }

    // 리다이렉트·하이드레이션·목 왕복이 끝날 때까지. mustSee 를 기다리는 게 가장 정확하다.
    await page
      .getByText(route.mustSee, { exact: false })
      .first()
      .waitFor({ state: "visible", timeout: 20_000 })
      .catch(() => problems.push(`화면에 "${route.mustSee}" 가 안 보인다`));

    // 🚨 "보이는가" 만으로는 부족하다 — 데이터를 기다리는 동안 비활성인 버튼도 글자는 보인다.
    //    실제로 status 조회가 죽어 로그인 버튼이 영구 비활성이던 버그가 있었다.
    //    ⚠️ 활성화될 때까지 기다린다. 한 번만 보면 아직 로딩 중인 버튼을 잡아 오탐이 난다.
    if (route.mustBeEnabled) {
      const button = page.getByRole("button", { name: route.mustBeEnabled }).first();
      const enabled = await button
        .waitFor({ state: "visible", timeout: 20_000 })
        .then(() =>
          // 🚨 글자만 보면 **아이콘 버튼을 원리상 못 잡는다** — `textContent` 가 비어 있다.
          //    이 저장소의 디자인 시스템은 아이콘 버튼을 정식 패턴으로 쓰고(`IconButton` 은
          //    `label` 을 필수로 받는다), 그 `label` 이 곧 접근성 이름이자 툴팁이다.
          //    "비활성 버튼도 글자는 보인다" 는 이 검사의 취지는 이름을 어디서 읽든 같다.
          page.waitForFunction(
            (name) =>
              [...document.querySelectorAll("button")].some((b) => {
                const label =
                  b.getAttribute("aria-label") ?? b.getAttribute("title") ?? b.textContent ?? "";
                return label.includes(name) && !b.disabled;
              }),
            route.mustBeEnabled,
            { timeout: 20_000 },
          ),
        )
        .then(() => true)
        .catch(() => false);
      if (!enabled) problems.push(`"${route.mustBeEnabled}" 버튼이 계속 비활성이다`);
    }

    const finalPath = new URL(page.url()).pathname;
    if (finalPath !== route.expect) {
      problems.push(`최종 경로가 ${route.expect} 가 아니라 ${finalPath}`);
    }

    // Next 개발 서버의 에러 오버레이. 떠 있으면 위 검사를 통과해도 화면이 깨진 것이다.
    if (await page.locator("nextjs-portal").count()) {
      const overlay = await page.locator("nextjs-portal").innerText().catch(() => "");
      if (/error|오류/i.test(overlay)) problems.push(`Next 에러 오버레이: ${overlay.slice(0, 120)}`);
    }

    // 🚨 가로 넘침. 390px 웹뷰 안에서 보는 화면들이라 가로 스크롤이 생기면 그 자체가 버그다.
    //    DOM 검사로는 절대 안 잡힌다 — 문구도 버튼도 다 정상인데 화면만 옆으로 밀려 있다.
    //    (레이아웃 검사 중 규칙으로 옮길 수 있는 건 사실상 이것뿐이다. 나머지는 PNG 를 봐야 한다.)
    const overflow = await page.evaluate(() => {
      const limit = document.documentElement.clientWidth;
      if (document.documentElement.scrollWidth <= limit + 1) return null;

      // 넘치는 요소는 부모까지 줄줄이 잡히므로, 가장 오른쪽까지 간 것 하나만 범인으로 지목한다.
      let worst = null;
      for (const el of document.body.querySelectorAll("*")) {
        const r = el.getBoundingClientRect();
        if (r.width === 0 || r.height === 0) continue;
        if (r.right <= limit + 1) continue;
        if (!worst || r.right > worst.right) worst = el;
      }
      const cls =
        typeof worst?.className === "string" && worst.className.trim()
          ? `.${worst.className.trim().split(/\s+/).slice(0, 3).join(".")}`
          : "";
      return {
        over: document.documentElement.scrollWidth - limit,
        limit,
        who: worst ? `${worst.tagName.toLowerCase()}${cls}` : "범인 못 찾음",
        right: Math.round(worst?.getBoundingClientRect().right ?? 0),
      };
    });
    if (overflow) {
      problems.push(
        `가로로 ${overflow.over}px 넘친다 (${overflow.limit}px 기준) — ${overflow.who} 가 ${overflow.right}px 까지`,
      );
    }

    // 개발 전용 떠 있는 버튼(Next 개발 표시기 · TanStack Query devtools)을 스크린샷에서 뺀다.
    // position:fixed 라 fullPage 로 찍으면 화면 중간 본문 위에 얹혀 찍힌다 — 실제로 소개 화면
    // "식사" 줄을 가렸고, 그림을 보는 사람이 없는 버그를 쫓게 된다. 🚨 위 오버레이 검사 뒤에 둘 것
    // (오버레이가 떠 있는지 먼저 봐야 하므로 순서를 바꾸면 에러 화면을 놓친다).
    await page.addStyleTag({
      content: "nextjs-portal, .tsqd-parent-container, [id^='tsqd'] { display: none !important; }",
    });

    const slug = route.name.replace(/[^0-9A-Za-z가-힣]+/g, "-").replace(/^-|-$/g, "");
    await page.screenshot({ path: join(OUT_DIR, `${slug}.png`), fullPage: true });
  } catch (e) {
    problems.push(`확인 중 실패: ${e instanceof Error ? e.message : String(e)}`);
  } finally {
    await ctx.close();
  }

  return { problems, warnings };
}

/* ── 실행 ──────────────────────────────────────────────────────────────── */

const { chromium } = loadPlaywright();
const executablePath = findChromium();
const { routes } = JSON.parse(readFileSync(join(SKILL_DIR, "routes.json"), "utf8"));
mkdirSync(OUT_DIR, { recursive: true });

const server = await ensureServer();
// 컨텍스트는 라우트마다 새로 만든다 (checkRoute). 웹뷰 폭 390×844 로 고정 —
// 이 화면들은 대부분 apps/mobile 안에서 보인다.
const browser = await chromium.launch({ executablePath, headless: !HEADED, args: ["--no-sandbox"] });

let failed = 0;
const allWarnings = new Set();
console.log("");
for (const route of routes) {
  const { problems, warnings } = await checkRoute(browser, route);
  for (const w of warnings) allWarnings.add(w);

  if (problems.length === 0) {
    console.log(`  PASS  ${route.name}`);
  } else {
    failed += 1;
    console.log(`  FAIL  ${route.name}`);
    for (const p of problems) console.log(`          ${p}`);
  }
  for (const w of warnings) console.log(`  warn      ${w}`);
}

await browser.close();
if (server && !KEEP_SERVER) {
  try {
    process.kill(-server.pid);
  } catch {
    /* 이미 죽음 */
  }
}

console.log(`\n${routes.length - failed}/${routes.length} 통과 · 스크린샷 ${OUT_DIR}`);
if (allWarnings.size > 0) {
  // 실패는 아니지만 계속 보이게 둔다 — 조용히 사라지면 아무도 안 고친다.
  console.log(`경고 ${allWarnings.size}건 — ${[...allWarnings].join(" · ")}`);
}
process.exit(failed === 0 ? 0 : 1);
