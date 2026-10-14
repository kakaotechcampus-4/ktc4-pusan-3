import type { Page } from "@playwright/test";

import { asOtherCaregiver, CHILD, expect, test } from "./fixtures";

/**
 * 10 설정 — 함께 보는 보호자 (#336 · #351).
 *
 * 누가 누구를 끊을 수 있는지가 **버튼이 서는 자리**로 화면에 나타나는지 본다. 서버가 다시
 * 막는 것(403 `owner_only` · 409 `owner_required`)은 계약 테스트가 건다.
 */

test.describe("owner 가 볼 때", () => {
  test("member 줄마다 연결 끊기가 서고, 자기 줄에는 없다", async ({ page }) => {
    await page.goto(`/child/${CHILD}/settings`);

    // 목록이 뜬 뒤에 센다 — 스켈레톤 동안에는 "버튼 0개" 가 그냥 성립한다.
    await expect(page.getByRole("button", { name: "연결 끊기" })).toHaveCount(2);
    const rows = page.getByRole("listitem");
    await expect(rows.filter({ hasText: "아이를 만든 보호자" }).getByRole("button")).toHaveCount(0);
    await expect(page.getByRole("button", { name: "나가기" })).toHaveCount(0);
  });

  test("이름이 빈 보호자는 관계 이름으로 부른다", async ({ page }) => {
    await page.goto(`/child/${CHILD}/settings`);

    const sitter = page.getByRole("listitem").filter({ hasText: "돌봄 선생님" });
    await sitter.getByRole("button", { name: "연결 끊기" }).click();

    const sheet = page.getByRole("dialog");
    await expect(sheet.getByText("돌봄 선생님이 더는 못 보게 할까요?")).toBeVisible();
    // 🚨 남는 것만 적으면 지워지는 것을 모르고 누른다 (#336).
    await expect(
      sheet.getByText("돌봄 선생님이 쓴 일기와 일정에 걸어 둔 알림은 지워져요."),
    ).toBeVisible();
  });
  /**
   * 🚨 **이미 끊긴 것은 실패가 아니다** (#351). `Idempotency-Key` 가 없어서 응답만 놓치고 다시
   *    누르면 404 다. 실패 카드를 세우면 다시 눌러도 같은 404 라 끝나지 않는다.
   */
  test("다른 곳에서 먼저 끊긴 보호자를 끊으면 실패 없이 시트가 닫히고 줄이 빠진다", async ({
    page,
  }) => {
    await page.goto(`/child/${CHILD}/settings`);
    const hyunwoo = page.getByRole("listitem").filter({ hasText: "현우" });
    await hyunwoo.getByRole("button", { name: "연결 끊기" }).click();
    const sheet = page.getByRole("dialog");
    await expect(sheet.getByText("현우가 더는 못 보게 할까요?")).toBeVisible();

    expect(
      await asOtherCaregiver(page, { method: "DELETE", path: `/children/${CHILD}/parents/p2` }),
    ).toBe(204);
    await sheet.getByRole("button", { name: "연결 끊기" }).click();

    await expect(page.getByRole("dialog")).toHaveCount(0);
    await expect(page.getByText("연결을 끊지 못했어요")).toHaveCount(0);
    await expect(page.getByRole("listitem").filter({ hasText: "현우" })).toHaveCount(0);
  });
});

test.describe("초대로 들어온 member 가 볼 때", () => {
  test.use({ scenario: "consent" });

  test("자기 줄에만 나가기가 서고, 나가면 /start 로 간다", async ({ page }) => {
    await joinAndOpenSettings(page);
    await expect(page.getByRole("button", { name: "연결 끊기" })).toHaveCount(0);

    await page.getByRole("button", { name: "나가기" }).click();
    const sheet = page.getByRole("dialog");
    await expect(sheet.getByText("함께 보기에서 나갈까요?")).toBeVisible();
    await expect(
      sheet.getByText("내가 적은 기록은 아이에게 남고, 적은 사람으로 내 이름이 보여요."),
    ).toBeVisible();

    await sheet.getByRole("button", { name: "나가기" }).click();
    await expect(page).toHaveURL(/\/start$/);
  });

  /**
   * 🚨 **나간 뒤 다시 보낸 나가기는 403 `child_access_denied` 다** (#351). 연결이 남은 채로 이 코드가
   *    오는 길은 없어서(서버 확인 · #352) 실패가 아니라 나간 것으로 보고 `/start` 로 간다.
   */
  test("이미 나간 뒤 다시 나가기를 눌러도 /start 로 간다", async ({ page }) => {
    const childId = await joinAndOpenSettings(page);
    await page.getByRole("button", { name: "나가기" }).click();
    const sheet = page.getByRole("dialog");
    await expect(sheet.getByText("함께 보기에서 나갈까요?")).toBeVisible();

    // 응답만 놓친 첫 요청 — 서버에서는 이미 나가졌다.
    expect(
      await asOtherCaregiver(page, { method: "DELETE", path: `/children/${childId}/parents/p1` }),
    ).toBe(204);
    await sheet.getByRole("button", { name: "나가기" }).click();

    await expect(page).toHaveURL(/\/start$/);
  });
});

/**
 * 초대를 수락하고 홈에서 설정까지 화면 안의 길로 간다. 연결된 아이 id 를 돌려준다.
 * 🚨 목 상태는 탭 안에만 산다 — 초대 수락부터 설정까지 `page.goto` 없이 이어 간다.
 */
async function joinAndOpenSettings(page: Page): Promise<string> {
  await page.goto("/invite");
  await page.getByLabel("초대 코드").fill("MKGRAND1");
  await page.getByRole("button", { name: "코드 확인하기" }).click();
  // 체크박스는 라벨이 덮고 있다 — 사람이 누르는 자리(글자)를 누른다.
  await page.getByText("만 19세 이상이에요").click();
  await expect(page.getByRole("checkbox", { name: /만 19세 이상이에요/ })).toBeChecked();
  await page.getByRole("button", { name: "연결하기" }).click();
  await expect(page).toHaveURL(/\/child\/[^/]+\/home$/);
  const childId = new URL(page.url()).pathname.split("/")[2]!;

  await page.getByRole("button", { name: "설정" }).click();
  await expect(page).toHaveURL(/\/settings$/);
  await expect(page.getByRole("button", { name: "나가기" })).toHaveCount(1);
  return childId;
}
