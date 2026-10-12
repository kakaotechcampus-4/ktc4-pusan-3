import { CHILD, expect, test } from "./fixtures";

/**
 * 10 설정 — 함께 보는 보호자 (#336 · #351).
 *
 * 누가 누구를 끊을 수 있는지가 **버튼이 서는 자리**로 화면에 나타나는지 본다. 서버가 다시
 * 막는 것(403 `owner_only` · 409 `owner_required`)은 계약 테스트가 건다.
 */

test.describe("owner 가 볼 때", () => {
  test("member 줄마다 연결 끊기가 서고, 자기 줄에는 없다", async ({ page }) => {
    await page.goto(`/child/${CHILD}/settings`);

    const rows = page.getByRole("listitem");
    await expect(rows.filter({ hasText: "아이를 만든 보호자" }).getByRole("button")).toHaveCount(0);
    await expect(page.getByRole("button", { name: "연결 끊기" })).toHaveCount(2);
    await expect(page.getByRole("button", { name: "나가기" })).toHaveCount(0);
  });

  test("이름이 빈 보호자는 관계 이름으로 부른다", async ({ page }) => {
    await page.goto(`/child/${CHILD}/settings`);

    const sitter = page.getByRole("listitem").filter({ hasText: "돌봄 선생님" });
    await sitter.getByRole("button", { name: "연결 끊기" }).click();

    const sheet = page.getByRole("dialog");
    await expect(sheet.getByText("돌봄 선생님이 더는 못 보게 할까요?")).toBeVisible();
    // 🚨 남는 것만 적으면 지워지는 것을 모르고 누른다 (#336).
    await expect(
      sheet.getByText("돌봄 선생님이 쓴 일기와 일정에 걸어 둔 알림은 지워져요."),
    ).toBeVisible();
  });
});

test.describe("초대로 들어온 member 가 볼 때", () => {
  test.use({ scenario: "consent" });

  test("자기 줄에만 나가기가 서고, 나가면 /start 로 간다", async ({ page }) => {
    // 🚨 목 상태는 탭 안에만 산다 — 초대 수락부터 설정까지 화면 안의 길로 이어 간다.
    await page.goto("/invite");
    await page.getByLabel("초대 코드").fill("MKGRAND1");
    await page.getByRole("button", { name: "코드 확인하기" }).click();
    // 체크박스는 라벨이 덮고 있다 — 사람이 누르는 자리(글자)를 누른다.
    await page.getByText("만 19세 이상이에요").click();
    await expect(page.getByRole("checkbox", { name: /만 19세 이상이에요/ })).toBeChecked();
    await page.getByRole("button", { name: "연결하기" }).click();
    await expect(page).toHaveURL(/\/child\/[^/]+\/home$/);

    await page.getByRole("button", { name: "설정" }).click();
    await expect(page).toHaveURL(/\/settings$/);

    await expect(page.getByRole("button", { name: "나가기" })).toHaveCount(1);
    await expect(page.getByRole("button", { name: "연결 끊기" })).toHaveCount(0);

    await page.getByRole("button", { name: "나가기" }).click();
    const sheet = page.getByRole("dialog");
    await expect(sheet.getByText("함께 보기에서 나갈까요?")).toBeVisible();
    await expect(
      sheet.getByText("내가 적은 기록은 아이에게 남고, 적은 사람으로 내 이름이 보여요."),
    ).toBeVisible();

    await sheet.getByRole("button", { name: "나가기" }).click();
    await expect(page).toHaveURL(/\/start$/);
  });
});
