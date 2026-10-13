import type { Page } from "@playwright/test";

import { CHILD, expect, recordApi, test } from "./fixtures";

/**
 * 10 설정 › 탈퇴 — 응답을 못 받은 탈퇴를 화면이 어떻게 끝내는가 (#389 · 서버 PR #384).
 *
 * 서버는 커밋하면서 그 보호자의 세션을 전부 지운다. 그래서 응답만 잃은 뒤의 401 은 만료가 아니라
 * **탈퇴가 끝난 것**이다 — 전역 401 처리로 새면 "탈퇴했어요" 대신 첫 화면이 뜨고, 돌아올 주소로
 * 이 화면이 남는다.
 */

const WITHDRAW = `/child/${CHILD}/settings/withdraw`;
/** `lib/auth/oauth.ts` 의 RETURN_KEY. 전역 401 처리가 여기에 돌아올 주소를 남긴다. */
const RETURN_KEY = "icatch.oauth.return";

async function confirmWithdraw(page: Page) {
  await page.goto(WITHDRAW);
  // 실제 input 은 숨겨져 있고 라벨이 누르는 자리다 (`components/ui/checkbox.tsx`).
  await page.getByText(/위 내용을 읽었고/).click();
  await expect(page.getByRole("checkbox", { name: /위 내용을 읽었고/ })).toBeChecked();
  await page.getByRole("button", { name: "탈퇴하기" }).click();
  const sheet = page.getByRole("dialog", { name: "정말 탈퇴할까요?" });
  await sheet.getByRole("button", { name: "탈퇴할게요" }).click();
  return sheet;
}

async function expectWithdrawnHere(page: Page) {
  await expect(page.getByRole("heading", { name: "탈퇴했어요" })).toBeVisible();
  expect(new URL(page.url()).pathname).toBe(WITHDRAW);
  expect(await page.evaluate((key) => sessionStorage.getItem(key), RETURN_KEY)).toBeNull();
}

test("탈퇴하면 끝 화면이 선다", async ({ page }) => {
  await confirmWithdraw(page);
  await expectWithdrawnHere(page);
});

test.describe("지운 뒤에 응답이 사라지면", () => {
  test.use({ scenario: "withdraw_lost", allowFailedApi: true });

  test("/me 로 확인하고 첫 화면이 아니라 '탈퇴했어요' 로 끝난다", async ({ page }) => {
    const calls = recordApi(page);
    await confirmWithdraw(page);
    await expectWithdrawnHere(page);

    // 🚨 확인은 탈퇴 **뒤에** 한 번이다. 순서가 바뀌면 지워지기 전의 계정을 보고 "그대로" 라고 한다.
    const order = calls
      .filter((c) => c.path === "/auth/withdraw" || c.path === "/me")
      .map((c) => `${c.method} ${c.path}`);
    expect(order.slice(-2)).toEqual(["POST /auth/withdraw", "GET /me"]);
  });
});

test.describe("응답도 확인도 끊기면", () => {
  test.use({ scenario: "withdraw_unconfirmed", allowFailedApi: true });

  /**
   * 🚨 **다시 누른 탈퇴가 401 을 받는 길이다.** 세션은 이미 지워졌으니 서버는 401 이고, 화면은 이것을
   *    만료가 아니라 "끝났다" 로 받아야 한다 — 전역 처리로 새면 첫 화면으로 튕기고 돌아올 주소가 남는다.
   */
  test("모른다고 말한 뒤 다시 누르면 401 을 '탈퇴했어요' 로 받는다", async ({ page }) => {
    const calls = recordApi(page);
    const sheet = await confirmWithdraw(page);
    await expect(sheet.getByText("처리됐는지 확인하지 못했어요")).toBeVisible();

    await sheet.getByRole("button", { name: "탈퇴할게요" }).click();
    await expectWithdrawnHere(page);
    expect(calls.filter((c) => c.method === "POST" && c.path === "/auth/withdraw")).toHaveLength(2);
  });
});

test.describe("서버에 닿기 전에 끊기면", () => {
  test.use({ scenario: "withdraw_unreached", allowFailedApi: true });

  test("'아직 지워지지 않았어요' 라고 말하고, 다시 누르면 탈퇴한다", async ({ page }) => {
    const sheet = await confirmWithdraw(page);
    await expect(sheet.getByText("확인해 보니 아직 지워지지 않았어요")).toBeVisible();
    await expect(sheet.getByText("처리됐는지 확인하지 못했어요")).toHaveCount(0);

    await sheet.getByRole("button", { name: "탈퇴할게요" }).click();
    await expectWithdrawnHere(page);
  });
});
