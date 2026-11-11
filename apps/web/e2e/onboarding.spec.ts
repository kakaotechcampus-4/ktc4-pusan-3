import { CHILD, expect, recordApi, switchScenario, test, writes } from "./fixtures";

/**
 * 02 아이 정보 — 서버 구현(PR #353)에 맞춘 것. 범위 밖 숫자는 보내기 전에 칸에 붙이고,
 * 값을 고쳐 다시 보내면 새 Idempotency-Key 가 나간다.
 * 02 성별 칸으로 공통 `ChoiceField` 의 라벨 연결(`for`/`id`)도 건다 (#685).
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
  // 🚨 화면은 기본 시나리오로 연다 — `consent` 는 아이 0명인 계정이라, 처음부터 그 시나리오면
  //    `c1` 이 내 아이가 아니어서 화면이 서지 않는다 (#802). 저장 직전에만 바꾼다.
  test("같은 값은 같은 키, 고친 값은 새 키다", async ({ page }) => {
    const calls = recordApi(page);
    await page.goto(ONBOARDING);
    const next = page.getByRole("button", { name: "다음" });
    await expect(page.getByLabel("키 (cm)")).toBeVisible();
    await switchScenario(page, "consent");

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

test("성별 라디오는 감싼 라벨과 for/id 로도 이어진다", async ({ page }) => {
  await page.goto(ONBOARDING);
  const radios = page.getByRole("group", { name: "성별" }).getByRole("radio");
  await expect(radios).toHaveCount(3);

  // 🚨 감싸기만으로는 접근성 검사(KWCAG)가 "레이블과 연결되지 않았다" 로 잡는다 (#685).
  const links = await radios.evaluateAll((inputs) =>
    inputs.map((input) => ({
      id: input.id,
      labelFor: input.closest("label")?.htmlFor ?? null,
    })),
  );
  for (const { id, labelFor } of links) {
    expect(id).not.toBe("");
    expect(labelFor).toBe(id);
  }
  // 그룹끼리 id 가 겹치지 않는 것은 접두어 `useId()` 가 보장한다 — 02 에는 그룹이 하나뿐이다.

  // 이어 둔 뒤에도 글자를 눌러 고를 수 있다.
  await page.locator("label", { hasText: "여자아이" }).click();
  await expect(page.getByRole("radio", { name: "여자아이" })).toBeChecked();
});
