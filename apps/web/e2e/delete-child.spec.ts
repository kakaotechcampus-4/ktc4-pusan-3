import type { Page } from "@playwright/test";

import { asOtherCaregiver, CHILD, expect, recordApi, test, writes } from "./fixtures";

/**
 * 11 아이 프로필 › 아이 삭제 (#751 · 서버 `DELETE /children/{cid}`).
 *
 * 아이 기본정보 · 건강정보 동의를 앱 안에서 거두는 길이다. 되돌릴 수 없어서 읽음 표시와 확인 시트를
 * 거쳐야만 지워지는지, 지운 뒤 `/start` 로 가는지, 등록 보호자에게만 길이 서는지를 본다.
 * 서버가 다시 막는 것(403 `owner_only`)은 목 계약 테스트가 건다.
 *
 * 🚨 목 상태는 탭 안에만 산다 — 프로필에서 지우기까지 `page.goto` 없이 화면 안의 길로 간다.
 */

const DELETE_PAGE = new RegExp(`/child/${CHILD}/profile/delete$`);

async function openDeletePage(page: Page) {
  await page.goto(`/child/${CHILD}/profile`);
  await page.getByRole("link", { name: /이 아이 삭제하기/ }).click();
  await expect(page).toHaveURL(DELETE_PAGE);
}

async function acknowledgeAndOpenSheet(page: Page) {
  // 실제 input 은 숨겨져 있고 라벨이 누르는 자리다 (`components/ui/checkbox.tsx`).
  await page.getByText(/위 내용을 읽었고/).click();
  await expect(page.getByRole("checkbox", { name: /위 내용을 읽었고/ })).toBeChecked();
  await page.getByRole("button", { name: "아이 삭제하기" }).click();
  return page.getByRole("dialog", { name: "정말 이 아이를 삭제할까요?" });
}

test("읽었다고 표시하고 확인 시트에서 한 번 더 눌러야 지워지고, 지운 뒤 /start 로 간다", async ({
  page,
}) => {
  const calls = recordApi(page);
  await openDeletePage(page);

  // 🚨 읽었다고 표시하기 전에는 못 누른다 — 되돌릴 장치가 없어서 실수를 막는 것이 이 둘뿐이다.
  await expect(page.getByRole("button", { name: "아이 삭제하기" })).toBeDisabled();
  const sheet = await acknowledgeAndOpenSheet(page);
  await expect(sheet).toBeVisible();
  expect(writes(calls, "DELETE")).toEqual([]);

  await sheet.getByRole("button", { name: "삭제할게요" }).click();

  // 목의 /me 에 아이가 하나뿐이라 지운 뒤에는 경로 고르기 화면이다.
  await expect(page).toHaveURL(/\/start$/);
  await expect(page.getByRole("heading", { name: "어떻게 시작할까요" })).toBeVisible();
  const deletes = writes(calls, "DELETE", /^\/children\/[^/]+$/);
  expect(deletes).toHaveLength(1);
  expect(deletes[0]!.path).toBe(`/children/${CHILD}`);
  // 🚨 되돌릴 수 없는 요청이라 키가 실린다 (`lib/api/idempotency.ts`).
  expect(deletes[0]!.idempotencyKey).toBeTruthy();
});

test("그대로 둘게요를 누르면 아무것도 지우지 않는다", async ({ page }) => {
  const calls = recordApi(page);
  await openDeletePage(page);
  const sheet = await acknowledgeAndOpenSheet(page);

  await sheet.getByRole("button", { name: "그대로 둘게요" }).click();

  await expect(sheet).toBeHidden();
  await expect(page).toHaveURL(DELETE_PAGE);
  expect(writes(calls, "DELETE")).toEqual([]);
});

/**
 * 🚨 **이미 지운 뒤 다시 누른 삭제는 403 `child_access_denied` 다** (서버 #751). 끝난 뒤에는 아이 접근 확인이
 *    키 재생보다 먼저라 그렇다. 등록 보호자에게 이 코드가 오는 길은 아이가 없어진 것뿐이라 실패가 아니라
 *    지워진 것으로 보고 `/start` 로 간다 — 응답만 잃고 다시 누른 경우다.
 */
test("이미 지워진 뒤 다시 눌러도 /start 로 간다", async ({ page }) => {
  await openDeletePage(page);
  const sheet = await acknowledgeAndOpenSheet(page);

  // 응답만 놓친 첫 요청 — 서버에서는 이미 지워졌다.
  expect(
    await asOtherCaregiver(page, {
      method: "DELETE",
      path: `/children/${CHILD}`,
      idempotent: true,
    }),
  ).toBe(204);
  await sheet.getByRole("button", { name: "삭제할게요" }).click();

  await expect(page).toHaveURL(/\/start$/);
});

test.describe("초대로 들어온 member 가 볼 때", () => {
  test.use({ scenario: "consent" });

  test("🚨 아이 프로필에 삭제 길이 서지 않는다 — 서버가 403 owner_only 로 막는 버튼을 그리지 않는다", async ({
    page,
  }) => {
    await page.goto("/invite");
    await page.getByLabel("초대 코드").fill("MKGRAND1");
    await page.getByRole("button", { name: "코드 확인하기" }).click();
    await page.getByText("만 19세 이상이에요").click();
    await page.getByRole("button", { name: "연결하기" }).click();
    await expect(page).toHaveURL(/\/child\/[^/]+\/home$/);

    await page.getByRole("link", { name: "아이", exact: true }).click();
    await expect(page).toHaveURL(/\/profile$/);
    await expect(page.getByRole("heading", { name: "기본 정보" })).toBeVisible();
    // 🚨 프로필을 받은 뒤에 센다 — 제목은 받기 전에도 서서, 그때 세면 role 과 상관없이 0 이다 (#865 에서 발견).
    await expect(page.getByText("2021년 4월 2일")).toBeVisible();
    await expect(page.getByRole("link", { name: /이 아이 삭제하기/ })).toHaveCount(0);
  });
});
