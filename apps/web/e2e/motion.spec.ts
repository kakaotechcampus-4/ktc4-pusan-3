import type { Page } from "@playwright/test";

import { CHILD, expect, test } from "./fixtures";

/**
 * 움직임 규칙 (디자인 시스템 §8 · #426). 움직임은 **바뀐 순간에만** 서고, 방향을 모르는 이동과 승인 게이트에는 없다.
 *
 * 움직임이 "보이는지" 는 스크린샷으로 못 건다 — 그래서 **무엇이 움직이기 시작했는지**를 기록한다.
 * 화면 전환은 `document.startViewTransition` 에 실린 방향(types)을, 화면 안의 움직임은 `Element.animate` 를
 * 가로채 적는다 (`lib/motion.ts` 는 전부 Web Animations 로 그린다).
 */

type Recorded = {
  /** 시작된 화면 전환마다 실린 방향. 방향 없는 전환은 빈 배열이다. */
  transitions: string[][];
  /**
   * 시작된 Web Animations. `flip` 은 자리 옮김(`data-flip`) 줄에 걸린 것. `tag` 가 `HTML` 인 것은 React 가
   * 화면 전환을 시작하며 문서 루트에 거는 것이라 화면 안의 움직임이 아니다.
   */
  animations: Array<{ tag: string; flip: boolean; keyframes: string }>;
};

async function recordMotion(page: Page) {
  await page.addInitScript(() => {
    const log: Recorded = { transitions: [], animations: [] };
    (window as unknown as { __motion: Recorded }).__motion = log;

    const start = document.startViewTransition?.bind(document);
    if (start) {
      document.startViewTransition = ((
        arg?: ViewTransitionUpdateCallback | StartViewTransitionOptions,
      ) => {
        const types = arg && typeof arg === "object" && arg.types ? [...arg.types] : [];
        log.transitions.push(types);
        return start(arg as StartViewTransitionOptions);
      }) as typeof document.startViewTransition;
    }

    const animate = Element.prototype.animate;
    Element.prototype.animate = function (keyframes, options) {
      log.animations.push({
        tag: this.tagName,
        flip: this instanceof HTMLElement && this.dataset.flip !== undefined,
        keyframes: JSON.stringify(keyframes),
      });
      return animate.call(this, keyframes, options);
    };
  });
}

function recorded(page: Page): Promise<Recorded> {
  return page.evaluate(() => (window as unknown as { __motion: Recorded }).__motion);
}

test.describe("화면 전환은 방향이 붙은 이동에서만 움직인다 (§8 허용하는 화면 전환)", () => {
  test("03 → 10 은 forward, 돌아가기 화살표는 back, 하단 네비는 방향이 없다", async ({ page }) => {
    await recordMotion(page);
    await page.goto(`/child/${CHILD}/home`);

    await page.getByRole("button", { name: "설정" }).click();
    await page.waitForURL(`**/child/${CHILD}/settings`);
    await expect.poll(async () => (await recorded(page)).transitions.at(-1)).toEqual(["forward"]);

    await page.getByRole("link", { name: "홈으로 돌아가기" }).click();
    await page.waitForURL(`**/child/${CHILD}/home`);
    await expect.poll(async () => (await recorded(page)).transitions.at(-1)).toEqual(["back"]);

    // 🚨 하단 네비는 어디가 앞인지 모른다 — 방향을 지어내지 않는다.
    const before = (await recorded(page)).transitions.length;
    await page.getByRole("link", { name: "캘린더" }).click();
    await page.waitForURL(`**/child/${CHILD}/calendar**`);
    await expect(page.getByRole("heading", { name: "캘린더" })).toBeVisible();
    const after = (await recorded(page)).transitions.slice(before);
    expect(after.flat()).not.toContain("forward");
    expect(after.flat()).not.toContain("back");
  });
});

test.describe("바텀시트 — 게이트 시트는 움직이지 않는다 (§8 하지 않는 것)", () => {
  test("되돌릴 수 있는 시트는 아래에서 올라오고, 알레르기 등록(게이트 ㉡) 시트는 그대로 선다", async ({
    page,
  }) => {
    await page.goto(`/child/${CHILD}/profile`);

    await page.getByRole("button", { name: "기본 정보 고치기" }).click();
    const plain = page.getByRole("dialog", { name: "기본 정보 고치기" });
    await expect(plain).toBeVisible();
    await expect(plain).toHaveClass(/\bbottom-sheet\b/);
    expect(await plain.evaluate((el) => getComputedStyle(el).transitionProperty)).toContain(
      "translate",
    );
    await plain.getByRole("button", { name: "그만두기" }).click();
    await expect(plain).toBeHidden();

    await page.getByRole("button", { name: "알레르기·건강 기록 추가" }).click();
    await page.getByRole("button", { name: /^직접 적기/ }).click();
    const gate = page.getByRole("dialog", { name: "알레르기 · 건강 기록 추가" });
    await expect(gate).toBeVisible();
    await expect(gate).not.toHaveClass(/\bbottom-sheet\b/);
    expect(await gate.evaluate((el) => getComputedStyle(el).translate)).toBe("none");
  });
});

test.describe("자리 옮김은 바뀐 순간에만 (11-1 측정 기록)", () => {
  async function addLog(page: Page) {
    await page.getByRole("button", { name: "키·몸무게 새로 적기" }).click();
    const sheet = page.getByRole("dialog", { name: "새로 재서 적기" });
    await sheet.getByLabel("키 · cm").fill("110.2");
    await sheet.getByRole("button", { name: "적어 두기" }).click();
    await expect(sheet).toBeHidden();
    await expect(page.getByText("110.2").first()).toBeVisible();
  }

  test("처음 그려질 때는 줄이 움직이지 않고, 새로 적으면 나머지 줄이 비켜난다", async ({
    page,
  }) => {
    await recordMotion(page);
    await page.goto(`/child/${CHILD}/profile/growth`);
    await expect(page.getByRole("button", { name: "이 기록 지우기" }).first()).toBeVisible();

    // 🚨 처음 그려진 목록은 서 있어야 한다 — 화면이 뜰 때 줄이 올라오면 등장 애니메이션이다.
    expect((await recorded(page)).animations.filter((a) => a.flip)).toEqual([]);

    await addLog(page);
    await expect
      .poll(async () => (await recorded(page)).animations.filter((a) => a.flip).length)
      .toBeGreaterThan(0);
    const flips = (await recorded(page)).animations.filter((a) => a.flip);
    expect(flips.some((a) => a.keyframes.includes("translate"))).toBe(true);
  });

  test("reduced-motion 에서는 위치가 움직이지 않고 투명도만 남는다", async ({ page }) => {
    await page.emulateMedia({ reducedMotion: "reduce" });
    await recordMotion(page);
    await page.goto(`/child/${CHILD}/profile/growth`);
    await expect(page.getByRole("button", { name: "이 기록 지우기" }).first()).toBeVisible();

    await addLog(page);
    await expect
      .poll(async () => (await recorded(page)).animations.filter((a) => a.flip).length)
      .toBeGreaterThan(0);
    const all = (await recorded(page)).animations;
    expect(all.filter((a) => a.keyframes.includes("translate"))).toEqual([]);
  });
});

test.describe("처음 그려질 때는 움직이지 않는다 (§8 원칙)", () => {
  test("받아 둔 화면으로 돌아오면 아무것도 움직이지 않는다 — 11 → 11-1 → 11", async ({ page }) => {
    await recordMotion(page);
    await page.goto(`/child/${CHILD}/profile`);
    await expect(page.getByRole("heading", { name: "아이 프로필" })).toBeVisible();
    await page.getByRole("link", { name: /cm|kg/ }).first().click();
    await page.waitForURL(`**/child/${CHILD}/profile/growth`);
    await expect(page.getByRole("button", { name: "이 기록 지우기" }).first()).toBeVisible();

    const before = (await recorded(page)).animations.length;
    await page.getByRole("link", { name: "아이 프로필" }).click();
    await page.waitForURL(`**/child/${CHILD}/profile`);
    await expect(page.getByRole("button", { name: "기본 정보 고치기" })).toBeVisible();
    // 화면 전환(back)이 끝날 때까지 기다린 뒤 본다 — 그 사이 늦게 걸리는 움직임도 잡는다.
    await page.waitForTimeout(600);

    // 🚨 캐시가 있어 스켈레톤 없이 선 화면이다. `Swap` · `useEnterMotion` · `useFlip` 중 무엇이든
    //    마운트 때 움직이면 여기 걸린다 — 그건 §8 이 막는 등장 애니메이션이다.
    const after = (await recorded(page)).animations.slice(before).filter((a) => a.tag !== "HTML");
    expect(after).toEqual([]);
  });
});
