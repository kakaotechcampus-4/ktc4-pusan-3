import { expect, test } from "./fixtures";

/**
 * 계정 삭제 안내 — Play Console 의 "계정 삭제 URL" 이 가리키는 페이지 (#166).
 *
 * 앱을 지웠거나 로그인할 수 없는 사람이 읽는다. 그래서 **로그인 없이** 열리고, 로그인 화면으로
 * 튕기지 않아야 한다. Google 이 요구하는 세 가지(앱 · 개발자 이름 / 지우는 단계 / 남는 데이터와
 * 기간)가 화면에 있는지도 함께 건다.
 */
test.describe("로그인 전", () => {
  test.use({ signedIn: false });

  test("로그인 없이 열리고, 지우는 방법과 남는 기간이 보인다", async ({ page }) => {
    await page.goto("/account-deletion");

    await expect(page.getByRole("heading", { name: "계정 삭제 안내" })).toBeVisible();
    await expect(page).toHaveURL(/\/account-deletion$/);

    await expect(page.getByText("아이캐치(운영: 트루독)", { exact: false })).toBeVisible();
    await expect(page.getByText("설정 › 계정 › 탈퇴하기")).toBeVisible();
    await expect(page.getByRole("link", { name: "parkhajh0803.dev@gmail.com" })).toHaveAttribute(
      "href",
      "mailto:parkhajh0803.dev@gmail.com",
    );
    await expect(page.getByText("최대 30일 뒤 전체 파기")).toBeVisible();

    // Play Console 의 "데이터 삭제 URL" 칸도 이 페이지다 — 계정을 지우지 않고 기록만 지우는 길.
    await expect(
      page.getByRole("heading", { name: "계정은 두고 기록만 지우고 싶을 때" }),
    ).toBeVisible();

    await expect(page.getByRole("link", { name: "개인정보 처리방침" })).toHaveAttribute(
      "href",
      /\/privacy-policy$/,
    );
  });
});
