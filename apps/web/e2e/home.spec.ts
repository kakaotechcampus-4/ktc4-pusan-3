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

/**
 * 03 홈 "오늘" — 급식 줄 (서버 `GET /children/{cid}/home` 의 `today` · `kind: "meal"`).
 * 🚨 급식은 저장된 그대로의 기록 표시다 — 판정 · "맞춤" 문구를 붙이지 않는다 (최상위 §2).
 */
test.describe("기록이 0건이어도 오늘 줄이 있는 홈", () => {
  test.use({ scenario: "empty_meal" });

  test("오늘 급식이 서고, 기록 0건 안내는 그대로다", async ({ page }) => {
    await page.goto(`/child/${CHILD}/home`);

    const today = page.locator('[data-tour="today"]');
    await expect(today.getByRole("heading", { name: "오늘", exact: true })).toBeVisible();
    await expect(today.getByText(/^점심 · /)).toBeVisible();
    await expect(today.getByText("기관 급식표").first()).toBeVisible();
    // 빈 상태는 그대로 — 기록 0건을 한 곳에서 말한다
    await expect(page.getByText("아래에 한 줄 적으면 여기에 쌓여요")).toBeVisible();
    await expect(page.getByText("쌓인 기록 0건", { exact: true })).toBeVisible();
    await expect(page.getByText(/기록 0건/)).toHaveCount(1);
    await expect(page.getByText("지금까지 함께 쌓은 기록", { exact: false })).toHaveCount(0);
    await expect(page.getByText(/맞춤/)).toHaveCount(0);
  });
});

test.describe("기록이 0건이고 오늘 줄도 없는 홈", () => {
  test.use({ scenario: "empty" });

  test("오늘 구역을 세우지 않는다", async ({ page }) => {
    await page.goto(`/child/${CHILD}/home`);

    await expect(page.getByText("아래에 한 줄 적으면 여기에 쌓여요")).toBeVisible();
    await expect(page.locator('[data-tour="today"]')).toHaveCount(0);
  });
});

test("기록이 있는 홈은 급식 줄이 끼니마다 일정 줄 앞에 선다", async ({ page }) => {
  await page.goto(`/child/${CHILD}/home`);

  const rows = page.locator('[data-tour="today"] p.text-ink');
  await expect(rows.first()).toBeVisible();
  const titles = await rows.allInnerTexts();
  const meals = titles.filter((t) => /^(오전 간식|점심|오후 간식) · /.test(t));
  expect(meals.length).toBeGreaterThan(1);
  expect(new Set(titles).size).toBe(titles.length); // 줄 key 가 제목이라 겹치면 안 된다
  expect(titles.slice(0, meals.length)).toEqual(meals); // 급식이 먼저, 일정이 뒤
});

/**
 * 띄어쓰기 없는 긴 메뉴 키 · 일정 제목이 카드 밖으로 넘쳐 페이지가 가로로 밀리면 안 된다.
 * 전역이 `keep-all` 이라 끊을 자리가 없다 — 제목은 `wrap-anywhere` 로 낱말 안에서도 줄을 바꾼다.
 */
test.describe("긴 오늘 줄 제목", () => {
  test.use({ scenario: "home_long_title" });

  for (const width of [390, 320]) {
    test(`${width}px 에서 가로로 넘치지 않는다`, async ({ page }) => {
      await page.setViewportSize({ width, height: 844 });
      await page.goto(`/child/${CHILD}/home`);
      await expect(page.getByText(/^점심 · /)).toBeVisible();

      const overflow = await page.evaluate(
        () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
      );
      expect(overflow).toBe(0);
    });
  }
});
