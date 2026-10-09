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

test.describe("알레르기 직접 등록은 승인 게이트 ㉡ (§2 안전)", () => {
  async function openAddSheet(page: import("@playwright/test").Page) {
    await page.goto(PROFILE);
    await page.getByRole("button", { name: "알레르기·건강 기록 추가" }).click();
    await page.getByRole("button", { name: /^직접 적기/ }).click();
    return page.getByRole("dialog", { name: "알레르기 · 건강 기록 추가" });
  }

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

  /**
   * ⚠️ **키가 큰 화면에서 돌린다.** 390×844 에서는 심각도 고르기 상자의 목록이 시트 본문 바닥에
   *    가려져서(목록 y 573~830 · 본문 바닥 703) 네 번째 항목부터 눌리지 않는다 — 항목을 보이게
   *    하려고 본문이 스크롤되는 순간 `Select` 가 닫힌다. `Select` 자체의 문제라 이 테스트가
   *    덮지 않는다.
   */
  test.describe("종류에 따라 심각도 선택지가 갈린다", () => {
    test.use({ viewport: { width: 390, height: 1400 } });

    test("알레르기는 검사 등급(Class)을 묻고, 보내는 값은 DB 가 받는 값이다", async ({ page }) => {
      const calls = recordApi(page);
      const sheet = await openAddSheet(page);

      await sheet.getByRole("combobox", { name: "검사 등급" }).click();
      await expect(page.getByRole("option")).toHaveText([
        "모르겠어요",
        "Class 0",
        "Class 1",
        "Class 2",
        "Class 3",
        "Class 4",
        "Class 5",
        "Class 6",
      ]);
      await page.getByRole("option", { name: "Class 3" }).click();
      await sheet.getByLabel("무엇인가요").fill("복숭아");
      await sheet.getByRole("button", { name: "확인했어요, 등록할게요" }).click();
      await expect(sheet).toBeHidden();

      const saved = writes(calls, "POST", /\/health-safety$/);
      expect(saved).toHaveLength(1);
      expect(saved[0].body).toMatchObject({
        type: "allergy",
        label: "복숭아",
        severity: "class_3",
      });
    });

    test("지병으로 바꾸면 선택지가 바뀌고, 앞서 고른 Class 는 남지 않는다", async ({ page }) => {
      const sheet = await openAddSheet(page);
      await sheet.getByRole("combobox", { name: "검사 등급" }).click();
      await page.getByRole("option", { name: "Class 3" }).click();

      await sheet.getByRole("combobox", { name: "종류" }).click();
      await page.getByRole("option", { name: "지병 · 만성질환" }).click();

      // Class 3 이 남아 있으면 DB 가 막는 조합이 된다
      const severity = sheet.getByRole("combobox", { name: "얼마나 심한가요" });
      await expect(severity).toContainText("모르겠어요");
      await severity.click();
      await expect(page.getByRole("option")).toHaveText(["모르겠어요", "가볍게", "보통", "심하게"]);
    });
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
