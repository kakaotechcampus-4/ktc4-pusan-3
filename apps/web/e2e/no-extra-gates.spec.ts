import { CHILD, EVALUATIVE, expect, expectNoGate, test, visibleText } from "./fixtures";

/**
 * 승인 게이트는 **딱 2곳**이다 — ㉠ 캘린더 쓰기 ㉡ 건강·알레르기 기록 확정 (최상위 CLAUDE.md §2 실행).
 * 늘리면 자동화가 무의미해지고, 줄이면 되돌릴 수 없는 것이 사람 없이 확정된다.
 *
 * 두 곳에서 **뜨는 것**은 각 화면 테스트가 건다 (suggestions · chat · profile · safety-scan).
 * 여기서는 **그 밖의 화면에 게이트 표식이 없는 것**을 건다. 브라우저로는 모든 경로를 돌 수 없어서
 * "어디서 부를 수 있는가" 는 `src/test/approval-gates.test.ts` 가 코드로 따로 막는다.
 */

test("03 홈", async ({ page }) => {
  await page.goto(`/child/${CHILD}/home`);
  await expect(page.getByText("이번 주 기록")).toBeVisible();
  await expectNoGate(page);
});

test("09 캘린더 — 일정이 있는 날을 열어도", async ({ page }) => {
  await page.goto(`/child/${CHILD}/calendar`);
  await page
    .getByRole("button", { name: /일정 있음/ })
    .first()
    .click();
  await expect(page.getByText("저장된 일정")).toBeVisible();
  await expectNoGate(page);
});

test("08 사진 확인 화면 — 저장은 게이트가 아니다 (일정 넣기 시트 밖)", async ({ page }) => {
  await page.goto(`/child/${CHILD}/home`);
  await page.getByRole("button", { name: "사진으로 적기" }).click();
  await page.getByRole("button", { name: /^알림장·식단표/ }).click();
  await page
    .getByRole("button", { name: /기기에 있는 최근 사진/ })
    .first()
    .click();
  await expect(page.getByText("알림장이나 식단표로 읽었어요.")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByRole("button", { name: "이 내용으로 저장" })).toBeVisible();
  await expectNoGate(page);
});

test("10 설정", async ({ page }) => {
  await page.goto(`/child/${CHILD}/settings`);
  await expect(page.getByText("함께 보는 보호자")).toBeVisible();
  await expect(page.getByText("위치정보").first()).toBeVisible();
  await expectNoGate(page);
});

test.describe("가입 · 첫 진입", () => {
  test("가입 동의", async ({ page }) => {
    await page.addInitScript(() => {
      sessionStorage.setItem("icatch.oauth.consent_code", "cc_mock");
      sessionStorage.setItem("icatch.oauth.provider", "kakao");
      sessionStorage.setItem("icatch.oauth.bind", "e2e".padEnd(43, "x"));
    });
    await page.goto("/auth/consent");
    await expect(page.getByRole("checkbox", { name: "[필수] 서비스 이용약관" })).toBeVisible();
    await expectNoGate(page);
  });

  test.describe("아이가 없는 계정", () => {
    test.use({ scenario: "consent" });

    test("초대 확인 단계", async ({ page }) => {
      await page.goto("/invite");
      await page.getByLabel("초대 코드").fill("MKGRAND1");
      await page.getByRole("button", { name: "코드 확인하기" }).click();
      await expect(page.getByRole("button", { name: "연결하기" })).toBeVisible();
      await expectNoGate(page);
    });

    test("01 아이 만들기", async ({ page }) => {
      await page.goto("/onboarding");
      await expect(page.getByLabel("아이 별명")).toBeVisible();
      await expectNoGate(page);
    });
  });

  test('02 아이 정보 — 성별 기본값은 "밝히지 않을래요", 평가 표현 없음', async ({ page }) => {
    await page.goto(`/child/${CHILD}/onboarding`);
    // 🚨 수집은 이름 · 생일 · 알레르기와 건강, 그리고 선택 항목. 성별의 기본값은 밝히지 않는 것이다 (§2).
    await expect(page.getByRole("radio", { name: "밝히지 않을래요" })).toBeChecked();
    await expect(page.getByText("우유", { exact: true })).toBeVisible();
    expect(await visibleText(page)).not.toMatch(EVALUATIVE);
    // 알레르기 구역의 추가 시트(게이트 ㉡)는 닫혀 있다 — 화면 자체에는 게이트가 없다.
    await expectNoGate(page);
  });
});
