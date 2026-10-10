import {
  CHILD,
  EVALUATIVE,
  expect,
  expectNoGate,
  gateMarks,
  recordApi,
  test,
  visibleText,
  writes,
} from "./fixtures";

/**
 * 11 아이 프로필 — 알레르기는 보호자만 확정하고(게이트 ㉡), 키 · 몸무게는 평가 없이 보여준다.
 */

const PROFILE = `/child/${CHILD}/profile`;

async function openAddSheet(page: import("@playwright/test").Page) {
  await page.goto(PROFILE);
  await page.getByRole("button", { name: "알레르기·건강 기록 추가" }).click();
  await page.getByRole("button", { name: /^직접 적기/ }).click();
  return page.getByRole("dialog", { name: "알레르기 · 건강 기록 추가" });
}

test.describe("알레르기 직접 등록은 승인 게이트 ㉡ (§2 안전)", () => {
  test("게이트 표식(caution 배너 · approve 버튼)이 서고 실수로 닫히지 않는다", async ({ page }) => {
    const sheet = await openAddSheet(page);
    await expect(sheet.getByText("보호자가 확인한 것만 적어주세요")).toBeVisible();
    const marks = await gateMarks(page, "dialog[open]");
    expect(marks).toContain("approve 버튼: 확인했어요, 등록할게요");
    expect(marks.some((m) => m.startsWith("caution 색:"))).toBe(true);

    await page.keyboard.press("Escape");
    await expect(sheet).toBeVisible();
    await sheet.getByRole("button", { name: "그만두기" }).click();
    await expect(sheet).toBeHidden();
  });

  test("Idempotency-Key 를 달고, 서버가 확정하기 전에는 목록에 먼저 그리지 않는다", async ({
    page,
  }) => {
    const calls = recordApi(page);
    const sheet = await openAddSheet(page);
    await sheet.getByLabel("무엇인가요").fill("땅콩");

    const list = page.locator("main").getByText("땅콩", { exact: true });
    await sheet.getByRole("button", { name: "확인했어요, 등록할게요" }).click();
    // 🚨 낙관적 업데이트 금지 — 응답 전에 목록에 서면 확정된 것처럼 보인다 (apps/web §3 에러).
    await expect(sheet.getByText("등록하는 중이에요")).toBeVisible();
    await expect(list).toHaveCount(0);

    await expect(sheet).toBeHidden();
    await expect(list).toBeVisible();
    const saved = writes(calls, "POST", /\/health-safety$/);
    expect(saved).toHaveLength(1);
    expect(saved[0].idempotencyKey).toBeTruthy();
    // 심각도를 고르지 않았으면 싣지 않는다 — "모르겠어요" 를 값으로 지어내지 않는다.
    expect(saved[0].body).not.toHaveProperty("severity");
  });
});

/**
 * 시트 본문은 스크롤되는 면이라, 아래쪽 고르기 상자의 목록이 본문 바닥에 잘린다. 열 때 목록이
 * 보이게 본문을 움직이고, 열린 뒤의 스크롤에는 닫지 않아야 잘린 항목까지 고를 수 있다 (#306).
 */
test.describe("시트 안 고르기 상자는 마지막 항목까지 고를 수 있다 (#306)", () => {
  test("열면 목록이 끝까지 보이고, 마지막 항목이 등록 요청에 실린다", async ({ page }) => {
    const calls = recordApi(page);
    const sheet = await openAddSheet(page);
    // 지병으로 고른다 — 심각도 상자의 이름과 마지막 값("심하게")이 알레르기와 달리 바뀌지 않는다 (#298).
    await sheet.getByRole("combobox", { name: "종류" }).click();
    await page.getByRole("option", { name: "지병 · 만성질환" }).click();
    await sheet.getByLabel("무엇인가요").fill("천식");

    await sheet.getByRole("combobox", { name: "얼마나 심한가요" }).click();
    const last = page.getByRole("option").last();
    await expect(last).toHaveText("심하게");
    await expect(last).toBeInViewport({ ratio: 1 });
    await last.click();
    await expect(page.getByRole("listbox")).toHaveCount(0);

    await sheet.getByRole("button", { name: "확인했어요, 등록할게요" }).click();
    await expect(sheet).toBeHidden();
    const saved = writes(calls, "POST", /\/health-safety$/);
    expect(saved).toHaveLength(1);
    expect(saved[0].body).toMatchObject({ label: "천식", severity: "severe" });
  });

  test("열린 채로 시트 본문을 스크롤해도 닫히지 않는다", async ({ page }) => {
    const sheet = await openAddSheet(page);
    const trigger = sheet.getByRole("combobox", { name: "얼마나 심한가요" });
    await trigger.click();
    await expect(page.getByRole("listbox")).toBeVisible();

    // 본문이 실제로 움직였는지부터 본다 — 안 움직였으면 이 테스트는 아무것도 확인하지 않는다.
    const body = sheet.locator(".overflow-y-auto").first();
    const before = await body.evaluate((el) => el.scrollTop);
    await trigger.hover();
    await page.mouse.wheel(0, 80);
    await expect.poll(() => body.evaluate((el) => el.scrollTop)).toBeGreaterThan(before);

    await expect(page.getByRole("listbox")).toBeVisible();
    await expect(trigger).toHaveAttribute("aria-expanded", "true");
  });
});

test.describe("키 · 몸무게는 최근 한 줄만, 평가 없이 (§2 개인정보)", () => {
  test("프로필에는 그래프도 평가 낱말도 없다", async ({ page }) => {
    await page.goto(PROFILE);
    await expect(page.getByText("키 104.2cm, 몸무게 17.1kg")).toBeVisible();
    await expect(page.locator(".recharts-wrapper")).toHaveCount(0);
    // 직전 값과의 차이(+1.2cm)도 변화를 평가하는 말이 된다.
    const text = await visibleText(page);
    expect(text).not.toMatch(EVALUATIVE);
    expect(text).not.toMatch(/[+-]\d+(\.\d+)?\s?(cm|kg)/);
  });

  test("측정 기록 새로 적기는 승인 게이트가 아니다", async ({ page }) => {
    await page.goto(PROFILE);
    await page.getByRole("button", { name: "키·몸무게 새로 적기" }).click();
    const sheet = page.getByRole("dialog", { name: "새로 재서 적기" });
    await expect(sheet).toBeVisible();
    await expectNoGate(page, "dialog[open]");
    await page.keyboard.press("Escape");
    await expect(sheet).toBeHidden();
  });
});
