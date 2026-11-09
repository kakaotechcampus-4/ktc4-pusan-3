import type { Page } from "@playwright/test";

import { ACTIVITY_PHOTO_ENABLED } from "@/lib/features";

import { CHILD, expect, test } from "./fixtures";

/**
 * 아이 활동 사진을 꺼 둔 동안 (`lib/features.ts` · #884) — 사진 시트 어디에도 그 줄이 서지 않고,
 * 화면 어디에도 "활동 태그" 를 말하지 않는다.
 *
 * 🚨 목에서도 꺼 둔 스위치라 이 파일이 목 위에서 돈다. 켜면 건너뛴다 — 켜는 PR 이 새 문구로 다시 건다.
 */
test.skip(ACTIVITY_PHOTO_ENABLED, "아이 활동 사진이 켜져 있다");

/** 고를 것이 하나라 시트는 2단계(어디서 가져오나)로 바로 열린다. */
async function expectDocumentOnlySheet(page: Page) {
  const sheet = page.getByRole("dialog");
  await expect(sheet.getByRole("heading", { name: "알림장·식단표" })).toBeVisible();
  await expect(sheet.getByRole("button", { name: /^사진 촬영/ })).toBeVisible();
  await expect(sheet.getByRole("button", { name: /^앨범에서 고르기/ })).toBeVisible();

  await expect(sheet.getByText("어떤 사진인가요?")).toHaveCount(0);
  await expect(sheet.getByText("아이 활동 사진")).toHaveCount(0);
  await expect(sheet.getByRole("button", { name: /다른 종류로 바꾸기/ })).toHaveCount(0);
  await expect(page.getByText(/활동 태그/)).toHaveCount(0);
}

test("03 홈 — 사진 시트가 알림장·식단표로 바로 열린다", async ({ page }) => {
  await page.goto(`/child/${CHILD}/home`);
  await page.getByRole("button", { name: "사진으로 적기" }).click();
  await expectDocumentOnlySheet(page);
});

test("04 대화 — 사진 시트가 알림장·식단표로 바로 열린다", async ({ page }) => {
  await page.goto(`/child/${CHILD}/chat`);
  await page.getByRole("button", { name: "사진으로 적기" }).click();
  await expectDocumentOnlySheet(page);
});

test("08 직접 들어온 경우 — 안내가 활동 사진을 말하지 않고, 시트도 같다", async ({ page }) => {
  await page.goto(`/child/${CHILD}/photos`);
  await expect(page.getByText(/알림장이나 식단표는 글자를 읽어/)).toBeVisible();
  await expect(page.getByText(/활동 사진|활동 태그/)).toHaveCount(0);

  await page.getByRole("button", { name: /^사진 고르기/ }).click();
  await expectDocumentOnlySheet(page);
});

test("다시 열어도 바로 2단계다 — 닫았다 열면 1단계로 되감지 않는다", async ({ page }) => {
  await page.goto(`/child/${CHILD}/home`);
  await page.getByRole("button", { name: "사진으로 적기" }).click();
  await page.getByRole("dialog").getByRole("button", { name: "닫기", exact: true }).click();
  await page.getByRole("button", { name: "사진으로 적기" }).click();
  await expectDocumentOnlySheet(page);
});
