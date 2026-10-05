import { CHILD, expect, expectNoGate, test } from "./fixtures";

/**
 * 07 기억 — 관찰 한 건과 승격된 기억을 섞지 않는다 (최상위 CLAUDE.md §2 기억 · §5 Correction).
 *
 * 🚨 Correction 은 **묻는 것이 대상마다 다르다.** 기록은 `once_only` · `wrong`, 기억은
 *    `need_more_observation` · `outdated` · `wrong`. `confirm` 은 이력에만 남고 화면에서 묻지 않는다.
 */

const MEMORIES = `/child/${CHILD}/memories`;

test.describe("교정 질문이 기록과 기억에서 다르다 (§5 Correction)", () => {
  test('기록에는 "이번만 그랬어요" · "잘못된 기록" 둘만 묻는다', async ({ page }) => {
    await page.goto(MEMORIES);
    await page.getByText("저녁에 계란말이를 또 찾았어요").click();
    const sheet = page.getByRole("dialog", { name: "기록 상세" });
    await expect(sheet.getByText("이 기록이 어떤가요?")).toBeVisible();

    const verdicts = sheet.getByRole("button", {
      name: /^(이번만 그랬어요|잘못된 기록|맞아요|기록이 더 필요해요|지금은 달라요)$/,
    });
    await expect(verdicts).toHaveText(["이번만 그랬어요", "잘못된 기록"]);
    // 교정은 되돌릴 수 있다 — 승인 게이트가 아니다.
    await expectNoGate(page, "dialog[open]");
  });

  test('기억에는 "기록이 더 필요해요" · "지금은 달라요" · "잘못된 기록" 셋만 묻는다', async ({
    page,
  }) => {
    await page.goto(`${MEMORIES}?tab=profile`);
    await page.getByText("계란 반찬").first().click();
    const sheet = page.getByRole("dialog", { name: "기억 상세" });
    await expect(sheet.getByText("이 기억이 맞나요?")).toBeVisible();

    const verdicts = sheet.getByRole("button", {
      name: /^(이번만 그랬어요|잘못된 기록|맞아요|기록이 더 필요해요|지금은 달라요)$/,
    });
    await expect(verdicts).toHaveText(["기록이 더 필요해요", "지금은 달라요", "잘못된 기록"]);
    await expectNoGate(page, "dialog[open]");
  });
});

test.describe("한 번의 관찰을 성향으로 확정하지 않는다 (§2 기억)", () => {
  test("한 번 본 것은 후보로 남고, 반복된 것만 확인됨이다", async ({ page }) => {
    await page.goto(`${MEMORIES}?tab=profile`);
    const confirmed = page.getByRole("button").filter({ hasText: "계란 반찬" });
    await expect(confirmed).toContainText("확인됨");
    await expect(confirmed).toContainText("서로 다른 3일에 기록됐어요");

    const candidate = page.getByRole("button").filter({ hasText: "물놀이" });
    await expect(candidate).toContainText("후보");
    await expect(candidate).toContainText("아직 한 번 봤어요");
    await expect(candidate).not.toContainText("확인됨");
  });

  test("기록 탭의 관찰 한 건에는 성향 표현이 붙지 않는다", async ({ page }) => {
    await page.goto(MEMORIES);
    const row = page.getByRole("button").filter({ hasText: "저녁에 계란말이를 또 찾았어요" });
    await expect(row).toBeVisible();
    await expect(row).not.toContainText("좋아해요");
    await expect(row).not.toContainText("확인됨");
  });
});

test.describe("6개월 지난 기억은 단독 근거로 쓰지 않는다 (§2 기억)", () => {
  test.use({ scenario: "stale" });

  test("카드와 상세가 그 사실을 글자로 말한다", async ({ page }) => {
    await page.goto(`${MEMORIES}?tab=profile`);
    await expect(
      page.getByText("6개월이 지나서 이 기억만으로는 추천을 만들지 않아요.").first(),
    ).toBeVisible();
    await expect(page.getByText("마지막으로 본 지 7개월이 지났어요").first()).toBeVisible();

    await page.getByText("6개월이 지나서 이 기억만으로는 추천을 만들지 않아요.").first().click();
    const sheet = page.getByRole("dialog", { name: "기억 상세" });
    await expect(
      sheet.getByText("이 프로필만으로는 추천을 만들지 않아요", { exact: false }),
    ).toBeVisible();
  });

  // 발견 ⑤ (#242) — `confirm` 은 화면에서 묻지 않기로 했는데(§5) 안내가 그 버튼을 누르라고 한다.
  test("상세 안내가 화면에 없는 버튼을 가리키지 않는다", async ({ page }) => {
    test.fail(true, '#242 발견 ⑤ — memory-detail-sheet.tsx 의 "맞아요를 눌러주세요"');
    await page.goto(`${MEMORIES}?tab=profile`);
    await page.getByText("6개월이 지나서 이 기억만으로는 추천을 만들지 않아요.").first().click();
    const sheet = page.getByRole("dialog", { name: "기억 상세" });
    await expect(sheet.getByText("이 기억이 맞나요?")).toBeVisible();
    await expect(sheet.getByText("맞아요를", { exact: false })).toHaveCount(0);
  });
});

test.describe("health 관찰은 진단 · 평가를 하지 않는다 (§2 안전)", () => {
  test("증상은 사실만 서고, 묶인 기억도 심각도 평가도 없다", async ({ page }) => {
    await page.goto(MEMORIES);
    await page.getByText("자다가 기침을 몇 번 했어요").click();
    const sheet = page.getByRole("dialog", { name: "기록 상세" });
    await expect(sheet.getByText("이 기록은 제안 근거에서 빠져 있어요.")).toBeVisible();
    await expect(sheet.getByText("묶인 기억")).toHaveCount(0);
    await expect(sheet.getByText(/가볍게|심해요|mild|severe/)).toHaveCount(0);
  });
});

test("07 세 탭 어디에도 승인 게이트 표식이 없다", async ({ page }) => {
  // 탭마다 데이터가 다 그려진 것을 확인하고 본다 — 빈 껍데기에서는 표식이 없는 게 당연하다.
  for (const [tab, loaded] of [
    ["", "기록 6건"],
    ["?tab=profile", "기억 2건"],
    ["?tab=feedback", "계란말이에 시금치를 조금 섞어 보세요"],
  ]) {
    await page.goto(`${MEMORIES}${tab}`);
    await expect(page.getByText(loaded).first()).toBeVisible();
    await expectNoGate(page);
  }
});
