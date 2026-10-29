import type { Page } from "@playwright/test";

import { PARENT_ID } from "@/mocks/fixtures";
import { seenStorageKey } from "@/lib/tour/types";

import { CHILD, expect, test } from "./fixtures";

/**
 * 화면 투어 (#632 · `docs/web/screen-tour-v1.md`).
 *
 * 다른 화면 테스트는 투어를 이미 본 채로 시작한다 (`fixtures.ts` 의 `toursSeen`). 여기서만 끈다.
 * 🚨 "봤음" 은 기기 저장소라 `page.goto` · `reload` 를 해도 남는다 — 목 상태와 달리 다시 들어와 볼 수 있다.
 */
test.use({ toursSeen: false });

/** 떠 있는 투어. 시트(`BottomSheet`)와 가르려고 설명 연결(`aria-describedby`)까지 본다. */
function tour(page: Page) {
  return page.locator("dialog[open][aria-describedby]");
}

/** 기기에 남은 "봤음" 목록. 🚨 "안 뜬다" 보다 이것이 먼저다 — 안 뜨는 것은 저장이 깨져도 잠깐은 성립한다. */
async function seenTours(page: Page): Promise<string[]> {
  const raw = await page.evaluate((key) => localStorage.getItem(key), seenStorageKey(PARENT_ID));
  return raw ? (JSON.parse(raw) as string[]) : [];
}

/**
 * 다시 들어온 화면에서 투어가 안 뜨는지. 🚨 바로 단언하지 않는다 — 자동 투어는 받은 내용이 그려질 때까지
 * 최대 1.5초 기다렸다 뜬다 (`components/screen-tour.tsx` 의 `AUTO_WAIT_MS`). 그 창이 지난 뒤에 본다.
 */
async function expectNoAutoTour(page: Page) {
  await expect(page.getByRole("heading", { name: "눈여겨볼 것" })).toBeVisible();
  await page.waitForTimeout(2000);
  await expect(tour(page)).toHaveCount(0);
}

test("처음 들어온 홈에서 저절로 뜨고, 마지막에 ? 로 다시 보는 단계가 붙는다", async ({ page }) => {
  await page.goto(`/child/${CHILD}/home`);

  await expect(tour(page)).toBeVisible();
  await expect(tour(page).getByRole("heading")).toHaveText("여기에 한 줄 적어 주세요");
  // 목 홈에는 대화 이어보기 문이 없다 — 비출 것이 없는 단계는 빠지고 분모도 같이 준다.
  const count = await tour(page)
    .getByText(/^\d+ \/ \d+$/)
    .innerText();
  const total = Number(count.split("/")[1]);

  for (let i = 1; i < total; i++) await tour(page).getByRole("button", { name: "다음" }).click();
  await expect(tour(page).getByRole("heading")).toHaveText("다시 보고 싶을 때");
  // 마지막 단계에는 그만 볼 남은 것이 없다 — "다 봤어요" 가 같은 일을 한다.
  await expect(tour(page).getByRole("button", { name: "그만 볼래요" })).toHaveCount(0);
  await tour(page).getByRole("button", { name: "다 봤어요" }).click();
  await expect(tour(page)).toHaveCount(0);
  await expect.poll(() => seenTours(page)).toContain("home");

  await page.reload();
  await expectNoAutoTour(page);
});

test("그만 봐도 본 것이고, ? 로 다시 열면 ? 안내 단계 없이 처음부터 본다", async ({ page }) => {
  await page.goto(`/child/${CHILD}/home`);
  await tour(page).getByRole("button", { name: "그만 볼래요" }).click();
  await expect(tour(page)).toHaveCount(0);
  await expect.poll(() => seenTours(page)).toContain("home");

  await page.reload();
  await expectNoAutoTour(page);
  await page.getByRole("button", { name: "이 화면 설명 보기" }).click();
  await expect(tour(page).getByRole("heading")).toHaveText("여기에 한 줄 적어 주세요");

  const count = await tour(page)
    .getByText(/^\d+ \/ \d+$/)
    .innerText();
  const total = Number(count.split("/")[1]);
  for (let i = 1; i < total; i++) await tour(page).getByRole("button", { name: "다음" }).click();
  await expect(tour(page).getByRole("heading")).not.toHaveText("다시 보고 싶을 때");
  await expect(tour(page).getByRole("button", { name: "다 봤어요" })).toBeVisible();
});

test("Esc 는 그만 보기다", async ({ page }) => {
  await page.goto(`/child/${CHILD}/home`);
  await expect(tour(page)).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(tour(page)).toHaveCount(0);
  await expect.poll(() => seenTours(page)).toContain("home");

  await page.reload();
  await expectNoAutoTour(page);
});

/** 🚨 "확인하고 넣기" 를 설명하다 그게 눌리면 승인 시트가 열린다 (`components/screen-tour.tsx` 머리말). */
test("투어 중에는 비춘 요소도 바깥도 눌리지 않는다", async ({ page }) => {
  await page.goto(`/child/${CHILD}/home`);
  await expect(tour(page).getByRole("heading")).toHaveText("여기에 한 줄 적어 주세요");

  // 비춘 입력창을 눌러도 입력창에 들어가지 않는다.
  const input = page.getByLabel("오늘 있었던 일");
  const box = await input.boundingBox();
  await page.mouse.click(box!.x + box!.width / 2, box!.y + box!.height / 2);
  await expect(input).not.toBeFocused();

  // 바깥(설정 톱니)을 눌러도 닫히지 않고 화면도 넘어가지 않는다.
  const settings = await page.getByRole("button", { name: "설정" }).boundingBox();
  await page.mouse.click(settings!.x + settings!.width / 2, settings!.y + settings!.height / 2);
  await expect(page).toHaveURL(new RegExp(`/child/${CHILD}/home$`));
  await expect(tour(page)).toBeVisible();
});

test.describe("기록이 없는 홈", () => {
  test.use({ scenario: "empty" });

  test("빈 상태를 설명하고, 기록이 생긴 홈의 단계는 꺼내지 않는다", async ({ page }) => {
    await page.goto(`/child/${CHILD}/home`);

    await expect(tour(page).getByRole("heading")).toHaveText("여기에 한 줄 적어 주세요");
    const headings: string[] = [];
    while ((await tour(page).getByRole("button", { name: "다음" }).count()) > 0) {
      await tour(page).getByRole("button", { name: "다음" }).click();
      headings.push(await tour(page).getByRole("heading").innerText());
    }
    expect(headings).toContain("아직 기록이 없어요");
    expect(headings).not.toContain("눈여겨볼 것");
    expect(headings).not.toContain("오늘 챙길 것");
  });
});

test("설정은 저절로 뜨지 않고, 화면 설명 다시 보기를 누르면 다음에 들어간 화면에서 다시 뜬다", async ({
  page,
}) => {
  await page.goto(`/child/${CHILD}/home`);
  await tour(page).getByRole("button", { name: "그만 볼래요" }).click();
  await expect.poll(() => seenTours(page)).toContain("home");

  await page.getByRole("button", { name: "설정" }).click();
  await expect(page.getByRole("heading", { name: "설정" })).toBeVisible();
  await expect(page.getByRole("button", { name: "다시 켜기" })).toBeEnabled();
  // 설정 투어는 안 봤는데도 뜨지 않는다 (`settingsTour.auto`). 자동 투어가 기다리는 창이 지난 뒤에 본다.
  await page.waitForTimeout(2000);
  await expect(tour(page)).toHaveCount(0);

  await page.getByRole("button", { name: "다시 켜기" }).click();
  await expect(page.getByText("다시 켰어요. 각 화면에 들어가면 다시 보여 드려요.")).toBeVisible();
  await expect.poll(() => seenTours(page)).toEqual([]);

  await page.getByRole("link", { name: "홈으로 돌아가기" }).click();
  await expect(tour(page)).toBeVisible();
});
