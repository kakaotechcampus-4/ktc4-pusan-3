import type { Page } from "@playwright/test";

import { ACTIVITY_PHOTO_ENABLED } from "@/lib/features";

import { CHILD, expect, test } from "./fixtures";

/**
 * 아이 활동 사진 (`lib/features.ts` 의 `ACTIVITY_PHOTO_ENABLED` · #884).
 *
 * - 맨 위 테스트는 🚨 **스위치와 상관없이 늘 돈다.** 제일 위험한 순간은 문구를 안 고치고 켰을 때라서
 *   (9월 설계의 "활동 태그만 뽑아요" 가 11-03 결정과 반대), 그때 이 테스트가 깨져야 한다 (#889 리뷰).
 *   켜는 PR 이 문구를 캘린더 방식으로 바꾸면 그대로 통과한다.
 *   ⚠️ 시트와 08 안내까지만 본다. 업로드 뒤 확인 화면(`photo-review.tsx` 의 활동 lane 문구)은 이 검사 밖이다.
 * - 아래 묶음은 꺼 둔 동안의 시트 모양이다. 켜면 건너뛴다 — 켜는 PR 이 새 시트 모양으로 다시 건다.
 */

/** 화면에 보이는 "활동 태그" 문구. 지난 설계의 말이라 켜져 있든 꺼져 있든 0 이어야 한다. */
const OLD_ACTIVITY_COPY = /활동 태그/;

test('🚨 켜져 있든 꺼져 있든 시트와 08 안내가 "활동 태그" 를 말하지 않는다', async ({ page }) => {
  await page.goto(`/child/${CHILD}/photos`);
  // 안내 문장은 켜는 PR 이 바꿀 수 있어서 제목으로 기다린다.
  await expect(page.getByRole("heading", { name: "사진 한 장이 한 줄을 대신해요" })).toBeVisible();
  await expect(page.getByText(OLD_ACTIVITY_COPY)).toHaveCount(0);

  await page.getByRole("button", { name: /^사진 고르기/ }).click();
  const sheet = page.getByRole("dialog");
  await expect(
    sheet.getByRole("button", { name: /^사진 촬영|^알림장·식단표/ }).first(),
  ).toBeVisible();
  await expect(sheet.getByText(OLD_ACTIVITY_COPY)).toHaveCount(0);

  // 켜져 있으면 1단계에 활동 사진 줄이 선다 — 그 2단계 머리말까지 본다.
  const activityRow = sheet.getByRole("button", { name: /^아이 활동 사진/ });
  if ((await activityRow.count()) > 0) {
    await activityRow.click();
    await expect(sheet.getByRole("button", { name: /^사진 촬영/ })).toBeVisible();
    await expect(sheet.getByText(OLD_ACTIVITY_COPY)).toHaveCount(0);
  }
});

test.describe("꺼 둔 동안 — 시트가 알림장·식단표로 바로 열린다", () => {
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
    // 2단계 머리말이 1단계 줄의 설명("준비물과 일시로 정리해요")을 이어받는다 (#889 리뷰).
    await expect(sheet.getByText(/준비물과 일시로 정리해요/)).toBeVisible();
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
});
