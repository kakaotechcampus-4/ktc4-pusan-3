import { CHILD, expect, expectNoGate, test } from "./fixtures";

/**
 * 07 기억 — 관찰 한 건과 승격된 기억을 섞지 않는다 (최상위 CLAUDE.md §2 기억 · §5 Correction).
 *
 * 🚨 Correction 은 **묻는 것이 대상마다 다르다.** 기록은 `once_only` · `wrong`, 기억은
 *    `need_more_observation` · `outdated` · `wrong`. `confirm`(맞아요)은 서버에도 없다 (#277).
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

/**
 * 🚨 고친 기록도 목록에 남는다 (#266 — 서버는 deleted 만 뺀다). 고치기는 active 기록에서만
 *    한다 (#277 — 아니면 409). 그래서 고친 줄은 칩으로 갈리고, 다시 열면 버튼 대신 안내가 선다.
 */
test.describe("고친 기록은 목록에 남고 다시 고치지 않는다 (#266 · #277)", () => {
  test("이번만 그랬어요로 고치면 줄에 고친 기록 칩이 서고, 다시 열면 버튼이 없다", async ({
    page,
  }) => {
    await page.goto(MEMORIES);
    const row = page.getByRole("button").filter({ hasText: "저녁에 계란말이를 또 찾았어요" });
    await expect(row).toContainText("계란 반찬");
    await row.click();
    const sheet = page.getByRole("dialog", { name: "기록 상세" });
    await sheet.getByRole("button", { name: "이번만 그랬어요", exact: true }).click();
    await sheet.getByRole("button", { name: "바꾸기" }).click();
    await expect(sheet.getByText("이번만 그랬어요로 반영했어요.")).toBeVisible();
    // 방금 고친 기록에 버튼이 다시 서면 누르는 순간 409 다.
    await expect(sheet.getByText("이 기록이 어떤가요?")).toHaveCount(0);
    await sheet.getByRole("button", { name: "닫기" }).last().click();

    // 고친 표시가 서고, 기억으로 세지 않으니 묶인 기억 칩은 사라진다.
    await expect(row).toContainText("이번만 그랬어요로 고친 기록");
    await expect(row).not.toContainText("계란 반찬");

    await row.click();
    await expect(sheet.getByText("이번만 그랬어요로 고친 기록이에요.")).toBeVisible();
    // 연결은 남지만 기억의 기록 수에는 들어가지 않는다 — "묶인 기억" 이라고 쓰지 않는다.
    await expect(sheet.getByText("세지 않는 기억")).toBeVisible();
    await expect(sheet.getByText("묶인 기억")).toHaveCount(0);
    await expect(sheet.getByText("이 기록이 어떤가요?")).toHaveCount(0);
  });

  test("상태로 고르면 그 상태의 기록만 서고, 건수도 그 상태의 것이다", async ({ page }) => {
    await page.goto(MEMORIES);
    await expect(page.getByText("기록 6건")).toBeVisible();
    await page.getByRole("button").filter({ hasText: "저녁에 계란말이를 또 찾았어요" }).click();
    const sheet = page.getByRole("dialog", { name: "기록 상세" });
    await sheet.getByRole("button", { name: "이번만 그랬어요", exact: true }).click();
    await sheet.getByRole("button", { name: "바꾸기" }).click();
    await expect(sheet.getByText("이번만 그랬어요로 반영했어요.")).toBeVisible();
    await sheet.getByRole("button", { name: "닫기" }).last().click();

    const status = page.getByRole("combobox", { name: "상태" });
    await status.click();
    // 🚨 이름은 고치기 버튼과 같은 말이다 — 누른 버튼 이름으로 찾는다.
    await expect(page.getByRole("option")).toHaveText([
      "전체",
      "고치지 않은 기록",
      "이번만 그랬어요",
      "잘못된 기록",
    ]);
    await page.getByRole("option", { name: "이번만 그랬어요" }).click();

    await expect(page).toHaveURL(/status=stand_alone/);
    await expect(page.getByText("기록 1건")).toBeVisible();
    await expect(
      page.getByRole("button").filter({ hasText: "저녁에 계란말이를 또 찾았어요" }),
    ).toBeVisible();
    await expect(
      page.getByRole("button").filter({ hasText: "계란말이만 두 그릇 먹었어요" }),
    ).toHaveCount(0);
  });
});

/**
 * 🚨 기억 고치기는 의견이다 (#277). 서버는 판정으로 상태를 바꾸지 않고 기록 수로만 다시 센다 —
 *    누르기 전에는 "목록에 그대로 있어요" 만 약속하고, 바뀐 것은 누른 뒤 응답으로 말한다.
 */
test.describe("기억 고치기는 기억을 목록에서 빼지 않는다 (#277)", () => {
  async function correctAffinity(page: import("@playwright/test").Page, label: string) {
    await page.goto(`${MEMORIES}?tab=profile`);
    await page.getByText("계란 반찬").first().click();
    const sheet = page.getByRole("dialog", { name: "기억 상세" });
    await sheet.getByRole("button", { name: label, exact: true }).click();
    await expect(sheet.getByText("기억은 목록에 그대로 있어요.", { exact: false })).toBeVisible();
    await expect(sheet.getByText("목록에서 빼요", { exact: false })).toHaveCount(0);
    await sheet.getByRole("button", { name: "바꾸기" }).click();
    return sheet;
  }

  test("상태가 그대로면 그대로라고 말한다", async ({ page }) => {
    const sheet = await correctAffinity(page, "지금은 달라요");

    await expect(sheet.getByText("기억의 상태는 바뀌지 않았어요.")).toBeVisible();
    await sheet.getByRole("button", { name: "닫기" }).last().click();
    await expect(page.getByText("계란 반찬").first()).toBeVisible();
  });

  test("상태가 바뀌면 직전 상태와 함께 말한다", async ({ page }) => {
    // 계란 반찬은 묶인 기록 3건으로 확인됨이다. wrong 이 기준을 올려 후보로 내려간다.
    const sheet = await correctAffinity(page, "잘못된 기록");

    await expect(sheet.getByText("기억이 확인됨에서 후보로 바뀌었어요.")).toBeVisible();
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
    // 🚨 후보는 성향으로 말하지 않는다 — 목의 물놀이는 polarity 1 이지만 "좋아해요" 가 없어야 한다 (#259).
    await expect(confirmed).toContainText("좋아해요");
    await expect(candidate).not.toContainText("좋아해요");
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
      sheet.getByText("이 기억만으로는 추천을 만들지 않아요", { exact: false }),
    ).toBeVisible();
  });

  // `confirm`(맞아요)은 없는 판정이다(§5). 한동안 안내가 "맞아요를 눌러주세요" 였다 (#242).
  test("상세 안내가 화면에 없는 버튼을 가리키지 않는다", async ({ page }) => {
    await page.goto(`${MEMORIES}?tab=profile`);
    await page.getByText("6개월이 지나서 이 기억만으로는 추천을 만들지 않아요.").first().click();
    const sheet = page.getByRole("dialog", { name: "기억 상세" });
    await expect(sheet.getByText("이 기억이 맞나요?")).toBeVisible();
    await expect(sheet.getByText("맞아요를", { exact: false })).toHaveCount(0);
  });
});

/** 목의 건강 관찰은 어제 날짜다 (`fixtures.ts` 의 `healthObservation`). 브라우저와 같은 지역 시간이다. */
function yesterday(): string {
  const d = new Date(Date.now() - 24 * 60 * 60 * 1000);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

test.describe("health 관찰은 진단 · 평가를 하지 않는다 (§2 안전)", () => {
  // 07 기록 탭에서는 빠졌다 (첫 배포 범위 밖 · #259). 같은 시트를 여는 09 캘린더에서 건다.
  test("증상은 사실만 서고, 묶인 기억도 심각도 평가도 없다", async ({ page }) => {
    await page.goto(`/child/${CHILD}/calendar?date=${yesterday()}`);
    await page.getByText("자다가 기침을 몇 번 했어요").click();
    const sheet = page.getByRole("dialog", { name: "기록 상세" });
    await expect(sheet.getByText("이 기록은 제안 근거에서 빠져 있어요.")).toBeVisible();
    await expect(sheet.getByText("묶인 기억")).toHaveCount(0);
    await expect(sheet.getByText(/가볍게|심해요|mild|severe/)).toHaveCount(0);
  });
});

test.describe("07 기록 탭은 서버(#266)가 주는 대로 그린다 (#269)", () => {
  test("생활습관 기록이 섞여도 멈추지 않고 '성장' 으로 선다", async ({ page }) => {
    await page.goto(`${MEMORIES}?domain=growth`);
    const row = page.getByRole("button").filter({ hasText: "자기 전에 혼자 양치하겠다고 했어요" });
    await expect(row).toContainText("성장");
    await expect(page.getByText("블록을 세면서 열까지 갔어요")).toBeVisible();

    await row.click();
    await expect(page.getByRole("dialog", { name: "기록 상세" })).toBeVisible();
  });

  test("분류에 '건강' 이 없고, 주소에 남아 있어도 전체로 읽는다", async ({ page }) => {
    await page.goto(`${MEMORIES}?domain=health`);
    await expect(page.getByText("기록 6건")).toBeVisible();

    await page.getByRole("combobox", { name: "분류" }).click();
    await expect(page.getByRole("option")).toHaveText(["전체", "식사", "놀이", "성장"]);
  });

  test.describe("한 장(20건)을 넘으면", () => {
    test.use({ scenario: "observations_many" });

    test("'기록 더 보기' 로 끝까지 이어 붙이고, 다 받으면 버튼이 사라진다", async ({ page }) => {
      await page.goto(MEMORIES);
      await expect(page.getByText("기록 25건")).toBeVisible();
      const rows = page
        .getByRole("list")
        .filter({ hasText: "저녁에 계란말이를 또 찾았어요" })
        .getByRole("listitem");
      await expect(rows).toHaveCount(20);

      await page.getByRole("button", { name: "기록 더 보기" }).click();
      await expect(rows).toHaveCount(25);
      await expect(page.getByRole("button", { name: "기록 더 보기" })).toHaveCount(0);
    });
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
