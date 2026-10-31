import { CHILD, expect, recordApi, test, writes } from "./fixtures";

/**
 * 02 아이 정보 — 서버 구현(PR #353)에 맞춘 것. 범위 밖 숫자는 보내기 전에 칸에 붙이고,
 * 값을 고쳐 다시 보내면 새 Idempotency-Key 가 나간다.
 */

const ONBOARDING = `/child/${CHILD}/onboarding`;

test("범위 밖 키는 보내지 않고 그 칸에 이유를 붙인다", async ({ page }) => {
  const calls = recordApi(page);
  await page.goto(ONBOARDING);

  await page.getByLabel("키 (cm)").fill("1042");
  await page.getByRole("button", { name: "다음" }).click();

  await expect(page.getByText("cm 단위로 20부터 220까지 적을 수 있어요")).toBeVisible();
  // 🚨 서버는 같은 값에 계속 400 이다 — 보내 봐야 "다시 시도" 만 되풀이된다.
  expect(writes(calls, "POST", /\/onboarding$/)).toHaveLength(0);
});

test.describe("응답을 못 받은 뒤 다시 보내기", () => {
  // 저장이 막히는 시나리오로 "실패한 뒤 다시 누르기" 를 만든다. 키 규칙만 본다.
  test.use({ scenario: "consent" });

  test("같은 값은 같은 키, 고친 값은 새 키다", async ({ page }) => {
    const calls = recordApi(page);
    await page.goto(ONBOARDING);
    const next = page.getByRole("button", { name: "다음" });

    await page.getByLabel("키 (cm)").fill("104.2");
    await next.click();
    await expect.poll(() => writes(calls, "POST", /\/onboarding$/).length).toBe(1);
    await next.click();
    await expect.poll(() => writes(calls, "POST", /\/onboarding$/).length).toBe(2);
    await page.getByLabel("키 (cm)").fill("104.5");
    await next.click();
    await expect.poll(() => writes(calls, "POST", /\/onboarding$/).length).toBe(3);

    const [first, retry, edited] = writes(calls, "POST", /\/onboarding$/).map(
      (c) => c.idempotencyKey,
    );
    expect(first).toBeTruthy();
    expect(retry).toBe(first);
    // 🚨 키가 그대로면 서버는 처음 응답을 재생해서 고친 값이 버려진다 (서버 PR #353).
    expect(edited).not.toBe(first);
  });
});
