import { expect, test } from "./fixtures";

/**
 * 00-1 경로 고르기 — 고른 두 갈래에서 같은 화살표로 돌아온다.
 *
 * 🚨 한쪽에만 돌아가는 길이 있으면, 초대를 받았는데 01 을 잘못 누른 사람이 그 화면에 갇힌다.
 *    거기서 아이를 만들어 버리면 초대를 받을 수 없다 (`409 child_already_exists` · #96).
 */
test.describe("아이가 없는 계정", () => {
  test.use({ scenario: "consent" });

  for (const [row, path] of [
    ["아이를 등록할게요", "/onboarding"],
    ["초대 코드를 받았어요", "/invite"],
  ] as const) {
    test(`${row} 를 고른 뒤 화살표로 00-1 에 돌아온다`, async ({ page }) => {
      await page.goto("/start");
      await page.getByRole("link", { name: new RegExp(row) }).click();
      await expect(page).toHaveURL(path);

      await page.getByRole("link", { name: "시작 방법 고르기로 돌아가기" }).click();
      await expect(page).toHaveURL("/start");
      await expect(page.getByText("먼저 등록해 버리면 초대를 받을 수 없어요")).toBeVisible();
    });
  }
});
