import type { Page } from "@playwright/test";

import { CHILD, expect, expectNoGate, test } from "./fixtures";

/**
 * 07 기억 — 관찰 한 건과 승격된 기억을 섞지 않는다 (최상위 CLAUDE.md §2 기억 · §5 Correction).
 *
 * 🚨 Correction 은 **묻는 것이 대상마다 다르다.** 기록은 `once_only` · `wrong`, 기억은
 *    `need_more_observation` · `outdated` · `wrong`. `confirm`(맞아요)은 서버에도 없다 (#277).
 */

const MEMORIES = `/child/${CHILD}/memories`;

/** 피드백 탭의 보기 전환. 숨은 라디오 위를 라벨이 덮고 있어 사람이 누르는 자리(글자)를 누른다 (`ChoiceField`). */
async function chooseView(page: Page, label: "피드백 전" | "피드백 완료") {
  await page.getByText(label, { exact: true }).click();
  await expect(page.getByRole("radio", { name: label })).toBeChecked();
}

/**
 * 필터 시트를 열어 고르고 적용한다 (#584). 분류 · 상태 · 기간은 목록 위가 아니라 시트 안에 있다.
 * `choices` 는 [상자 이름, 고를 항목] 이다.
 */
async function applyFilter(page: Page, choices: Array<[string, string]>) {
  await page.getByRole("button", { name: /^필터/ }).click();
  const sheet = page.getByRole("dialog", { name: /필터$/ });
  for (const [label, option] of choices) {
    await sheet.getByRole("combobox", { name: label }).click();
    await page.getByRole("option", { name: option, exact: true }).click();
  }
  await sheet.getByRole("button", { name: "적용하기" }).click();
}

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
    // 연결은 남지만 기억의 기록 수에는 들어가지 않는다 — "관련된 기억" 이라고 쓰지 않는다.
    await expect(sheet.getByText("세지 않는 기억")).toBeVisible();
    await expect(sheet.getByText("관련된 기억")).toHaveCount(0);
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

    await page.getByRole("button", { name: /^필터/ }).click();
    const filter = page.getByRole("dialog", { name: "기록 필터" });
    await filter.getByRole("combobox", { name: "상태" }).click();
    // 🚨 이름은 고치기 버튼과 같은 말이다 — 누른 버튼 이름으로 찾는다.
    await expect(page.getByRole("option")).toHaveText([
      "전체",
      "고치지 않은 기록",
      "이번만 그랬어요",
      "잘못된 기록",
    ]);
    await page.getByRole("option", { name: "이번만 그랬어요" }).click();
    await filter.getByRole("button", { name: "적용하기" }).click();

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

test.describe("07 기록 탭은 서버(#266)가 주는 대로 그린다 (#269)", () => {
  // 🚨 필터는 Agent("성장")로 고르고, 내려온 기록은 종류("교육" · "생활습관")로 선다 (#501).
  test("'성장' 을 고르면 교육과 생활습관 기록이 각자 이름으로 함께 선다", async ({ page }) => {
    await page.goto(`${MEMORIES}?domain=growth`);
    const routine = page
      .getByRole("button")
      .filter({ hasText: "자기 전에 혼자 양치하겠다고 했어요" });
    const education = page.getByRole("button").filter({ hasText: "블록을 세면서 열까지 갔어요" });
    await expect(routine).toContainText("생활습관");
    await expect(education).toContainText("교육");
    await expect(routine).not.toContainText("성장");
    await expect(education).not.toContainText("성장");

    await routine.click();
    const sheet = page.getByRole("dialog", { name: "기록 상세" });
    await expect(sheet).toBeVisible();
    await expect(sheet.getByText("생활습관", { exact: true })).toBeVisible();
  });

  test("분류에 '건강' 이 없고, 주소에 남아 있어도 전체로 읽는다", async ({ page }) => {
    await page.goto(`${MEMORIES}?domain=health`);
    await expect(page.getByText("기록 6건")).toBeVisible();

    await page.getByRole("button", { name: /^필터/ }).click();
    await page
      .getByRole("dialog", { name: "기록 필터" })
      .getByRole("combobox", { name: "분류" })
      .click();
    await expect(page.getByRole("option")).toHaveText(["전체", "식사", "놀이", "성장"]);
  });

  test.describe("한 쪽(20건)을 넘으면 (#584)", () => {
    test.use({ scenario: "observations_many" });

    const rowsOf = (page: Page) =>
      page.getByRole("list").filter({ hasText: "블록으로 탑을 쌓았어요" }).getByRole("listitem");

    test("다음 쪽으로 넘기면 나머지가 서고, 건수는 그 쪽이 아니라 전체 수다", async ({ page }) => {
      await page.goto(MEMORIES);
      await expect(page.getByText("기록 25건")).toBeVisible();
      await expect(rowsOf(page)).toHaveCount(20);

      const pager = page.getByRole("navigation", { name: "기록 쪽 넘기기" });
      await expect(pager.getByRole("button", { name: "이전 쪽" })).toBeDisabled();
      await pager.getByRole("button", { name: "다음 쪽" }).click();

      await expect(page).toHaveURL(/[?&]page=2/);
      await expect(rowsOf(page)).toHaveCount(5);
      await expect(page.getByText("기록 25건")).toBeVisible();
      await expect(pager.getByRole("button", { name: "다음 쪽" })).toBeDisabled();
    });

    test("쪽 번호로 바로 가고, 조건을 바꾸면 1쪽으로 돌아간다", async ({ page }) => {
      await page.goto(MEMORIES);
      const pager = page.getByRole("navigation", { name: "기록 쪽 넘기기" });
      // 🚨 지금 쪽은 색만이 아니라 aria-current 로도 말한다.
      await expect(pager.getByRole("button", { name: "1쪽" })).toHaveAttribute(
        "aria-current",
        "page",
      );
      await pager.getByRole("button", { name: "2쪽" }).click();
      await expect(rowsOf(page)).toHaveCount(5);
      await expect(pager.getByRole("button", { name: "2쪽" })).toHaveAttribute(
        "aria-current",
        "page",
      );

      // 25건이 모두 고치지 않은 기록이라 바꾼 뒤에도 2쪽이 있다 — 쪽이 남으면 5줄과 page=2 가 남는다.
      // (쪽이 하나로 줄어드는 조건을 고르면 마지막 쪽 옮기기가 1쪽으로 데려가서 이 규칙을 못 본다.)
      await applyFilter(page, [["상태", "고치지 않은 기록"]]);
      await expect(page).toHaveURL(/[?&]status=active/);
      await expect(page).not.toHaveURL(/[?&]page=/);
      await expect(rowsOf(page)).toHaveCount(20);
      await expect(pager.getByRole("button", { name: "1쪽" })).toHaveAttribute(
        "aria-current",
        "page",
      );
    });

    test("마지막 쪽을 넘긴 주소는 빈 목록이 아니라 마지막 쪽을 세운다", async ({ page }) => {
      await page.goto(`${MEMORIES}?page=9`);
      await expect(page).toHaveURL(/[?&]page=2/);
      await expect(rowsOf(page)).toHaveCount(5);
      await expect(page.getByText("이 조건에 맞는 기록이 없어요")).toHaveCount(0);
    });
  });

  test("쪽이 하나뿐이면 쪽 넘기기를 그리지 않는다", async ({ page }) => {
    await page.goto(MEMORIES);
    await expect(page.getByText("기록 6건")).toBeVisible();
    await expect(page.getByRole("navigation", { name: "기록 쪽 넘기기" })).toHaveCount(0);
  });
});

test.describe("검색과 필터 (#584)", () => {
  test("검색어를 보내면 그 말이 든 기록만 서고, 칸을 비우면 전체로 돌아간다", async ({ page }) => {
    await page.goto(MEMORIES);
    await expect(page.getByText("기록 6건")).toBeVisible();

    const search = page.getByRole("searchbox", { name: "찾기" });
    await search.fill("계란말이");
    // 🚨 치는 동안에는 부르지 않는다 — 보내야 찾는다.
    await expect(page).not.toHaveURL(/[?&]q=/);
    await search.press("Enter");

    await expect(page.getByText("기록 3건")).toBeVisible();
    await expect(
      page.getByRole("button").filter({ hasText: "물놀이터에서 안 나오려고 했어요" }),
    ).toHaveCount(0);
    // 🚨 찾은 뒤에도 칸에 포커스가 남는다 — 칸을 갈아 끼우면 키보드가 닫히고 이어 친 글자가 사라진다.
    await expect(search).toBeFocused();

    // 🚨 비우고 기다리지 않고 바로 친다 — 주소가 바뀌기 전에 친 글자를 주소가 덮으면 안 된다.
    await search.fill("");
    await search.pressSequentially("물놀이");
    await expect(page.getByText("기록 6건")).toBeVisible();
    await expect(page).not.toHaveURL(/[?&]q=/);
    await expect(search).toBeFocused();
    await expect(search).toHaveValue("물놀이");
  });

  test("검색어 지우기 아이콘은 칸을 비우고 전체로 돌아가며, 포커스를 칸에 남긴다", async ({
    page,
  }) => {
    await page.goto(`${MEMORIES}?q=${encodeURIComponent("계란말이")}`);
    await expect(page.getByText("기록 3건")).toBeVisible();

    await page.getByRole("button", { name: "검색어 지우기" }).click();
    const search = page.getByRole("searchbox", { name: "찾기" });
    await expect(page.getByText("기록 6건")).toBeVisible();
    await expect(search).toHaveValue("");
    await expect(search).toBeFocused();
    // 빈 칸에는 지우기가 서지 않는다.
    await expect(page.getByRole("button", { name: "검색어 지우기" })).toHaveCount(0);
  });

  test("보내지 않은 검색어는 다른 조건을 바꾸면 칸에서 지운다", async ({ page }) => {
    await page.goto(MEMORIES);
    const search = page.getByRole("searchbox", { name: "찾기" });
    await search.fill("물놀이");

    await applyFilter(page, [["분류", "식사"]]);

    await expect(page.getByText("기록 3건")).toBeVisible();
    await expect(search).toHaveValue("");
  });

  test("필터는 적용해야 걸리고, 걸린 조건은 칩으로 남아 하나씩 풀린다", async ({ page }) => {
    await page.goto(MEMORIES);
    await page.getByRole("button", { name: /^필터/ }).click();
    const sheet = page.getByRole("dialog", { name: "기록 필터" });
    await sheet.getByRole("combobox", { name: "분류" }).click();
    await page.getByRole("option", { name: "식사", exact: true }).click();
    // 🚨 고르기만 해서는 걸지 않는다 — 시트 뒤의 목록은 그대로다.
    await expect(page).not.toHaveURL(/[?&]domain=/);
    await sheet.getByRole("button", { name: "적용하기" }).click();

    await expect(page).toHaveURL(/[?&]domain=food/);
    await expect(page.getByText("기록 3건")).toBeVisible();
    await expect(page.getByRole("button", { name: /^필터 1/ })).toBeVisible();

    await page.getByRole("button", { name: "식사 조건 풀기" }).click();
    await expect(page.getByText("기록 6건")).toBeVisible();
    await expect(page.getByRole("button", { name: /조건 풀기$/ })).toHaveCount(0);
  });

  test("기간은 한 칸의 달력에서 두 날을 차례로 고르고, 다시 열면 첫 누름이 새 기간이다", async ({
    page,
  }) => {
    await page.goto(MEMORIES);
    const filter = page.getByRole("dialog", { name: "기록 필터" });
    const calendar = page.getByRole("dialog", { name: "기간" });
    const openCalendar = async () => {
      await page.getByRole("button", { name: /^필터/ }).click();
      await filter.getByRole("button", { name: /^기간(?! 지우기)/ }).click();
    };
    const day = (n: number) => calendar.getByRole("button", { name: new RegExp(`${n}일`) }).first();

    await openCalendar();
    // 🚨 지난달로 넘긴 뒤 고른다 — 이번 달은 오늘 뒤의 날이 꺼져 있어서, 고르는 날이 실행 날짜에 따라 막힌다.
    await calendar.getByRole("button", { name: "이전 달" }).click();
    await day(1).click();
    await day(2).click();
    await calendar.getByRole("button", { name: "고르기" }).click();
    await filter.getByRole("button", { name: "적용하기" }).click();
    await expect(page).toHaveURL(/[?&]from=\d{4}-\d{2}-01&to=\d{4}-\d{2}-02/);
    // 🚨 기호(~)가 아니라 글자로 잇는다 — 스크린리더가 "부터 · 까지" 를 읽는다.
    await expect(page.getByRole("button", { name: /1일부터 .*2일까지 조건 풀기$/ })).toBeVisible();

    // 다시 열면 걸린 기간의 달에서 연다. 🚨 새 두 날을 차례로 누르면 그 두 날이다 — 끝만 옮기지 않는다.
    await openCalendar();
    await day(10).click();
    await day(20).click();
    await calendar.getByRole("button", { name: "고르기" }).click();
    await filter.getByRole("button", { name: "적용하기" }).click();
    await expect(page).toHaveURL(/[?&]from=\d{4}-\d{2}-10&to=\d{4}-\d{2}-20/);

    // 한 날만 누르고 고르면 그날 하루다.
    await openCalendar();
    await day(5).click();
    await calendar.getByRole("button", { name: "고르기" }).click();
    await filter.getByRole("button", { name: "적용하기" }).click();
    await expect(page).toHaveURL(/[?&]from=(\d{4}-\d{2}-05)&to=\1/);
  });

  test("기억 이름은 띄어쓰기 없이 쳐도 찾고, 필터 시트가 날짜 기준을 글자로 적는다", async ({
    page,
  }) => {
    await page.goto(`${MEMORIES}?tab=profile`);
    const search = page.getByRole("searchbox", { name: "찾기" });
    await search.fill("계란반찬");
    await search.press("Enter");

    await expect(page.getByText("기억 1건")).toBeVisible();
    await expect(page.getByRole("button").filter({ hasText: "계란 반찬" })).toBeVisible();
    await expect(page.getByRole("button").filter({ hasText: "물놀이" })).toHaveCount(0);

    await page.getByRole("button", { name: /^필터/ }).click();
    await expect(
      page.getByRole("dialog", { name: "기억 필터" }).getByText("마지막으로 기록된 날로 찾아요."),
    ).toBeVisible();
  });

  test("기간에 든 기록이 없으면 조건의 빈 상태를 세우고, 칩을 풀면 전체로 돌아간다", async ({
    page,
  }) => {
    await page.goto(`${MEMORIES}?from=2020-01-01&to=2020-01-31`);
    await expect(page.getByText("이 조건에 맞는 기록이 없어요")).toBeVisible();

    await page.getByRole("button", { name: "2020년 1월 1일부터 1월 31일까지 조건 풀기" }).click();
    await expect(page.getByText("기록 6건")).toBeVisible();
    await expect(page).not.toHaveURL(/[?&](from|to)=/);
  });

  test.describe("주소의 기간이 쓸 수 없는 값이면 실패 화면 대신 기간 없이 읽는다", () => {
    for (const [name, query] of [
      ["거꾸로 된 기간", "from=2026-10-31&to=2026-10-01"],
      ["한쪽만 있는 기간", "from=2020-01-01"],
      ["달력에 없는 날", "from=2026-02-31&to=2026-03-01"],
    ]) {
      test(name, async ({ page }) => {
        await page.goto(`${MEMORIES}?${query}`);
        await expect(page.getByText("기록 6건")).toBeVisible();
        await expect(page.getByRole("button", { name: /조건 풀기$/ })).toHaveCount(0);
      });
    }
  });
});

test.describe("피드백 탭의 일반 추천 (#509)", () => {
  test("채택한 일반 추천은 또래 기준이라고 적고, 개인화처럼 평가할 수 있다", async ({ page }) => {
    await page.goto(`${MEMORIES}?tab=feedback`);
    const general = "블록 쌓기처럼 손을 많이 쓰는 놀이를 15분쯤 해 보세요";
    const row = page.locator("li").filter({ hasText: general });
    await expect(row).toContainText("또래 기준 일반 추천");
    // 🚨 개인화 줄에는 그 라벨이 없다 — 우리 아이 기준 제안을 일반 추천으로 부르지 않는다.
    await expect(page.locator("li").filter({ hasText: "또래 기준 일반 추천" })).toHaveCount(1);

    await row.getByRole("button", { name: "아이가 좋아했어요" }).click();
    await expect(row).toHaveCount(0);

    // 평가는 피드백 완료에 남는다 — 일반 추천 라벨도 그대로다.
    await chooseView(page, "피드백 완료");
    const done = page.locator("li").filter({ hasText: general });
    await expect(done).toContainText("또래 기준 일반 추천");
    await expect(done.getByRole("button", { name: "아이가 좋아했어요" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    await expectNoGate(page);
  });
});

test.describe("피드백 탭의 두 보기 (#592)", () => {
  const PENDING = "두부를 부쳐서 한 조각 곁들여 보세요";
  const RATED = "계란말이에 시금치를 조금 섞어 보세요";

  test("처음 열면 피드백 전이고, 평가한 제안은 보이지 않는다", async ({ page }) => {
    await page.goto(`${MEMORIES}?tab=feedback`);
    await expect(page.getByRole("radio", { name: "피드백 전" })).toBeChecked();
    await expect(page.getByText(PENDING)).toBeVisible();
    await expect(page.getByText("제안 2건")).toBeVisible();
    await expect(page.getByText(RATED)).toHaveCount(0);
  });

  test("피드백 완료로 바꾸면 주소에 남고, 남긴 피드백이 켜진 채로 선다", async ({ page }) => {
    await page.goto(`${MEMORIES}?tab=feedback`);
    await chooseView(page, "피드백 완료");

    await expect(page).toHaveURL(/[?&]view=rated/);
    const row = page.locator("li").filter({ hasText: RATED });
    await expect(row.getByRole("button", { name: "아이가 좋아했어요" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    await expect(page.getByText(PENDING)).toHaveCount(0);

    // 다시 고르면 덮어쓰고, 줄은 그 보기에 남는다.
    await row.getByRole("button", { name: "안 해봤어요" }).click();
    await expect(row.getByRole("button", { name: "안 해봤어요" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    await expect(page.getByText("제안 1건")).toBeVisible();
  });

  test("🚨 피드백 전이 비어도 받은 제안이 없다고 하지 않는다", async ({ page }) => {
    await page.goto(`${MEMORIES}?tab=feedback`);
    for (const text of [PENDING, "블록 쌓기처럼 손을 많이 쓰는 놀이를 15분쯤 해 보세요"]) {
      const row = page.locator("li").filter({ hasText: text });
      await row.getByRole("button", { name: "안 해봤어요" }).click();
      await expect(row).toHaveCount(0);
    }
    await expect(page.getByText("피드백을 기다리는 제안이 없어요")).toBeVisible();
    await expect(page.getByText(/받은 제안이 없어요/)).toHaveCount(0);
  });

  test.describe("한 쪽(20건)을 넘으면", () => {
    test.use({ scenario: "suggestions_many" });

    test("🚨 마지막 쪽의 마지막 줄을 평가하면 빈 쪽이 아니라 앞 쪽으로 옮긴다", async ({
      page,
    }) => {
      await page.goto(`${MEMORIES}?tab=feedback&page=2`);
      await expect(page.getByText("제안 21건")).toBeVisible();
      const last = page.locator("li").filter({ hasText: "블록으로 탑을 함께 쌓아 보세요 (19)" });
      await last.getByRole("button", { name: "아이가 좋아했어요" }).click();

      await expect(page).not.toHaveURL(/[?&]page=/);
      await expect(page.getByText("제안 20건")).toBeVisible();
      await expect(page.getByText("피드백을 기다리는 제안이 없어요")).toHaveCount(0);
      await expect(page.getByRole("navigation", { name: "제안 쪽 넘기기" })).toHaveCount(0);
    });
  });
});

test("07 세 탭 어디에도 승인 게이트 표식이 없다", async ({ page }) => {
  // 탭마다 데이터가 다 그려진 것을 확인하고 본다 — 빈 껍데기에서는 표식이 없는 게 당연하다.
  for (const [tab, loaded] of [
    ["", "기록 6건"],
    ["?tab=profile", "기억 2건"],
    ["?tab=feedback", "두부를 부쳐서 한 조각 곁들여 보세요"],
  ]) {
    await page.goto(`${MEMORIES}${tab}`);
    await expect(page.getByText(loaded).first()).toBeVisible();
    await expectNoGate(page);
  }
});
