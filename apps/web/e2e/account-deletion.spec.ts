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

  test("데이터 삭제 주소의 #data 가 기록만 지우는 구역을 가리킨다", async ({ page }) => {
    // Play Console 의 "데이터 삭제 URL" 칸에 `/account-deletion#data` 를 넣었다 — `id` 가 바뀌거나
    // 다른 구역으로 옮겨 가면 이 테스트가 먼저 깨진다.
    // 🚨 스크롤 위치는 재지 않는다. 목 모드는 MSW 가 뜰 때까지 화면을 비워 두어서(`providers.tsx`)
    //    브라우저가 앵커로 내려가는 순간 그 구역이 아직 없다. 실서버 빌드(`next start`)에서는
    //    첫 HTML 에 구역이 있어 바로 내려간다 (#166 에서 손으로 확인).
    await page.goto("/account-deletion#data");

    await expect(
      page.locator("#data").getByRole("heading", { name: "계정은 두고 기록만 지우고 싶을 때" }),
    ).toBeVisible();
  });
});
