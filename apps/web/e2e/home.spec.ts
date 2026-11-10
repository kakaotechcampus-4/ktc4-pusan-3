import { CHILD, expect, test } from "./fixtures";

/**
 * 03 홈 (#868). 기록이 0건인 홈은 건수를 숨기지 않되 **한 곳에서만** 말하고, 무엇을 적으면 되는지 예를 든다.
 * 🚨 0건을 지우는 것이 아니다 — 빈 상태가 "쌓인 기록 0건" 을 말한다 (최상위 §2 · 디자인 시스템 §7 `empty-state`).
 */
test.describe("기록이 없는 홈", () => {
  test.use({ scenario: "empty" });

  test("기록 0건을 한 번만 말하고, 적을 말의 예를 든다", async ({ page }) => {
    await page.goto(`/child/${CHILD}/home`);

    await expect(page.getByText("아래에 한 줄 적으면 여기에 쌓여요")).toBeVisible();
    await expect(page.getByText("쌓인 기록 0건", { exact: true })).toBeVisible();
    await expect(page.getByText(/기록 0건/)).toHaveCount(1);
    await expect(page.getByText(/처럼 있었던 일을 그대로 적어 주세요/)).toBeVisible();
  });
});
