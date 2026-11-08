import { expect, registerChild, test } from "./fixtures";

/**
 * 가입한 보호자의 첫 흐름 — 00-1 → 01 아이 만들기(아이 동의 2건 포함) → 02 아이 정보 → 03 홈 (#756).
 *
 * 목 e2e 가 못 보는 것: 01 이 보낸 동의가 서버의 약관 버전 검사를 통과하는가, 02 가 저장한 값이
 * 다른 화면에서 다시 읽히는가, 새로고침해도(= 앱을 다시 켜도) refresh 쿠키로 같은 자리에 돌아오는가.
 * 목은 상태가 탭 메모리에만 있어서 새로고침에서 모두 사라진다.
 */

const NICKNAME = "테스트아이";

test("아이를 만들고 키를 적으면, 새로고침한 뒤에도 홈과 프로필에 그대로 있다", async ({ page }) => {
  const childId = await registerChild(page, NICKNAME);

  // 02 — 선택 항목 중 키 하나만 적는다.
  await page.getByLabel("키 (cm)").fill("104.2");
  await page.getByRole("button", { name: "다음" }).click();

  // 03 홈.
  await expect(page).toHaveURL(`/child/${childId}/home`);
  await expect(page.getByText(NICKNAME).first()).toBeVisible();

  // 새로고침 — access 는 메모리에만 있어서 사라지고, refresh 쿠키로 다시 받는다.
  await page.reload();
  await expect(page).toHaveURL(`/child/${childId}/home`);
  await expect(page.getByText(NICKNAME).first()).toBeVisible();

  // 02 가 저장한 키가 11 아이 프로필에서 다시 읽힌다.
  await page.goto(`/child/${childId}/profile`);
  await expect(page.getByText("104.2", { exact: false }).first()).toBeVisible();
});
