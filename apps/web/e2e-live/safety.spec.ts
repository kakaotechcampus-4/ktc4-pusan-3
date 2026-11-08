import { expect, registerChild, test } from "./fixtures";

/**
 * 11 아이 프로필 — 알레르기 직접 등록(승인 게이트 ㉡)이 서버에 확정되고, 다시 열어도 남아 있다 (#756).
 *
 * 게이트 표식 · Idempotency-Key · 낙관적 업데이트 금지 같은 화면 규칙은 목 e2e(`e2e/profile.spec.ts`)가
 * 건다. 여기서는 **서버가 받아 저장한 것**이 다시 읽히는지만 본다. 목 응답은 손으로 적은 것이라,
 * 서버가 돌려주는 모양이나 저장 규칙(이름 정규화 등)과 어긋나도 목 e2e 는 모른다.
 */

test("알레르기를 등록하면 새로고침한 뒤에도 목록에 있다", async ({ page }) => {
  const childId = await registerChild(page, "테스트아이");
  await page.getByRole("button", { name: "지금은 건너뛰기" }).click();
  await expect(page).toHaveURL(`/child/${childId}/home`);

  await page.goto(`/child/${childId}/profile`);
  await page.getByRole("button", { name: "알레르기·건강 기록 추가" }).click();
  // 검사지 읽기가 실서버에 없으면(`lib/features.ts` · #409) 고를 것이 직접 적기 하나라 시트가 바로 선다.
  const sheet = page.getByRole("dialog", { name: "알레르기 · 건강 기록 추가" });
  await sheet.evaluate((el) => Promise.all(el.getAnimations().map((a) => a.finished)));
  await sheet.getByLabel("무엇인가요").fill("땅콩");
  await sheet.getByRole("button", { name: "확인했어요, 등록할게요" }).click();
  await expect(sheet).toBeHidden();

  const peanut = page.locator("main").getByText("땅콩", { exact: true });
  await expect(peanut).toBeVisible();

  await page.reload();
  await expect(peanut).toBeVisible();
});
