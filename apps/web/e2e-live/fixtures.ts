import { execFileSync } from "node:child_process";

import { test as base, expect, type Page } from "@playwright/test";

import { TOUR_KEYS } from "@/lib/tour/types";

import { API_DIR, API_ORIGIN, WEB_ORIGIN, apiEnv } from "./server-env";

/**
 * 실서버 e2e 의 공통 바탕 (#756).
 *
 * 테스트마다 **가입이 막 끝난 보호자**를 새로 심고(`apps/api/scripts/seed_e2e.py`), 그 refresh 를
 * 쿠키로 심은 채 시작한다. 카카오 주소가 서버 코드의 상수라 카카오 로그인을 거칠 수 없어서다.
 * 화면은 운영과 똑같이 앱을 켤 때 `POST /auth/refresh` 로 access 를 받는다 — 그 길도 같이 걸린다.
 *
 * 🚨 목 e2e(`e2e/fixtures.ts`)와 섞어 쓰지 않는다. 그쪽은 MSW 표식을 심고, 여기는 진짜 쿠키를 심는다.
 */

type Seeded = { refresh_token: string; parent_id: string };

type Fixtures = {
  /** 이 테스트의 보호자. 아이는 없다 — 테스트가 01 화면에서 실제 API 로 만든다. */
  parent: Seeded;
};

/** refresh 쿠키 — 서버의 `_set_refresh` (`apps/api/app/api/v1/routers/auth.py`)와 같은 속성이다. */
// `__Host-` 쿠키는 Path=/ · Secure · Domain 없음이어야 브라우저가 받는다 (#783). domain 에 점을 안 붙이면
// Playwright 는 host-only 로 심는다.
const REFRESH_COOKIE = "__Host-icatch_rt";
const AUTH_COOKIE_PATH = "/";

function seedParent(): Seeded {
  const out = execFileSync("uv", ["run", "--no-sync", "python", "-m", "scripts.seed_e2e"], {
    cwd: API_DIR,
    env: { ...process.env, ...apiEnv() },
    encoding: "utf8",
  });
  // 스크립트는 마지막 줄에 JSON 하나만 낸다. 앞줄은 라이브러리 경고일 수 있다.
  return JSON.parse(out.trim().split("\n").at(-1) ?? "") as Seeded;
}

/**
 * 이 보호자가 투어를 다 본 것으로 서버에 남기고, 회전된 refresh 를 돌려준다 (#696).
 *
 * 처음 들어온 화면마다 투어 덮개(모달 `<dialog>`)가 서서 배경이 inert 가 된다 — 덮개가 먼저 서면 테스트의
 * 누르기가 막힌다. 목 e2e 의 `toursSeen` 과 같은 바탕이다.
 * 🚨 **access 를 받으려고 refresh 를 한 번 쓴다 → 쿠키가 회전한다.** 브라우저에는 시드의 값이 아니라 여기서
 *    받은 새 값을 심는다. 옛 값을 심으면 유예(30초)가 지난 뒤 재사용으로 보고 세션이 지워진다.
 */
async function markToursSeen(refreshToken: string): Promise<string> {
  const refreshed = await fetch(`${API_ORIGIN}/api/v1/auth/refresh`, {
    method: "POST",
    headers: { Cookie: `${REFRESH_COOKIE}=${refreshToken}` },
  });
  expect(refreshed.status, "투어 기록을 심으려고 받은 refresh").toBe(200);
  const { token } = (await refreshed.json()) as { token: string };
  const rotated = refreshed.headers
    .getSetCookie()
    .map((line) => line.split(";")[0] ?? "")
    .find((pair) => pair.startsWith(`${REFRESH_COOKIE}=`))
    ?.slice(REFRESH_COOKIE.length + 1);
  expect(rotated, "회전된 refresh 쿠키").toBeTruthy();

  for (const key of TOUR_KEYS) {
    const res = await fetch(`${API_ORIGIN}/api/v1/me/tours/${key}`, {
      method: "PUT",
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(res.status, `투어 ${key} 기록`).toBe(204);
  }
  return rotated!;
}

export const test = base.extend<Fixtures>({
  parent: async ({}, provide) => {
    const seeded = seedParent();
    await provide({ ...seeded, refresh_token: await markToursSeen(seeded.refresh_token) });
  },

  // 🚨 두 번째 인자를 `use` 로 부르지 않는다 — React 의 `use` 훅으로 읽혀 lint 가 막는다.
  page: async ({ page, parent }, provide) => {
    await page.context().addCookies([
      {
        name: REFRESH_COOKIE,
        value: parent.refresh_token,
        domain: "localhost",
        path: AUTH_COOKIE_PATH,
        httpOnly: true,
        secure: true,
        sameSite: "Strict",
      },
    ]);

    // 화면 투어는 `parent` 바탕이 서버에 다 본 것으로 남겨 두었다 (`markToursSeen`).

    // 🚨 이 두 서버 밖으로 나간 요청은 실패로 본다. api 의 키를 다 비웠어도 브라우저가 직접 부르는
    //    곳(지도 · 폰트 CDN 등)이 생기면 CI 가 외부에 기대게 된다.
    const outside: string[] = [];
    page.on("request", (request) => {
      const url = request.url();
      if (url.startsWith("data:") || url.startsWith("blob:")) return;
      if (url.startsWith(WEB_ORIGIN) || url.startsWith(API_ORIGIN)) return;
      outside.push(new URL(url).origin);
    });

    await provide(page);

    expect(outside, "e2e 서버 밖으로 나간 요청").toEqual([]);
  },
});

export { expect };

/** 지금 화면의 `/child/{childId}/…` 에서 아이 id 를 읽는다. */
export function childIdOf(page: Page): string {
  const match = new URL(page.url()).pathname.match(/^\/child\/([^/]+)\//);
  if (!match) throw new Error(`아이 화면이 아니다: ${page.url()}`);
  return match[1];
}

/**
 * 막 가입한 보호자가 00-1 → 01 로 아이를 만든다. 02 아이 정보 화면에 선 채로 아이 id 를 돌려준다.
 *
 * 아이는 시드로 만들지 않는다. 01 이 아이 동의 2건을 아이와 한 트랜잭션으로 보내는데(#96), 그 길이
 * 서버의 약관 버전 검사를 통과하는지가 이 스위트가 보려는 것 중 하나다.
 */
export async function registerChild(page: Page, nickname: string): Promise<string> {
  // 아이가 없는 보호자는 00-1 경로 고르기로 간다 (#96).
  await page.goto("/");
  await page.getByRole("link", { name: /아이를 등록할게요/ }).click();
  await expect(page).toHaveURL(/\/onboarding$/);

  await page.getByLabel("아이 별명").fill(nickname);
  await page.getByRole("button", { name: /^생일/ }).click();
  const calendar = page.getByRole("dialog");
  // 이번 달에서 고를 수 있는 첫날. 오늘 뒤는 막혀 있어서(`toDate`) 언제 돌려도 하나는 있다.
  await calendar
    .getByRole("gridcell")
    .getByRole("button")
    .and(page.locator(":enabled"))
    .first()
    .click();
  await calendar.getByRole("button", { name: "고르기" }).click();
  await expect(calendar).toBeHidden();

  // 아이 동의 2건과 법정대리인 확인. 체크박스는 눈에 안 보이는 input 이고 라벨이 덮고 있다
  // (`e2e/safety-scan.spec.ts` 와 같이 force). 🚨 무엇에 동의하는지는 서버(`GET /policies`)가 정한다 —
  // 항목 이름을 여기 적지 않는다. 약관이 바뀌어도 이 도우미는 그대로다.
  // 🚨 `all()` 은 기다리지 않는다. 약관(`GET /policies`)이 오기 전이면 법정대리인 확인 하나뿐이라,
  //    그것만 체크하고 "시작하기" 가 꺼진 채로 남는다. 약관 항목이 그려진 뒤에 고른다.
  await expect.poll(() => page.getByRole("checkbox").count()).toBeGreaterThan(1);
  for (const box of await page.getByRole("checkbox").all()) {
    if (!(await box.isChecked())) await box.check({ force: true });
  }
  await page.getByRole("button", { name: "시작하기" }).click();

  await expect(page).toHaveURL(/\/child\/[^/]+\/onboarding$/);
  return childIdOf(page);
}
