import { expect, test } from "./fixtures";

/**
 * 00 소개 · 로그인 (#704). 로그인 흐름 자체는 `session.spec.ts` · `splash-shell.spec.ts` 가 건다.
 * 여기는 소개가 지켜야 하는 화면 규칙이다.
 */

test.use({ signedIn: false });

const KAKAO = { name: "카카오로 시작하기" } as const;

test.describe("좁은 폰에서", () => {
  test.use({ viewport: { width: 360, height: 740 } });

  test("🚨 로그인 버튼이 첫 화면 안에 있다 — 다시 로그인하러 온 사람이 스크롤하지 않는다", async ({
    page,
  }) => {
    await page.goto("/");
    const first = page.getByRole("button", KAKAO).first();
    await expect(first).toBeEnabled();

    await expect(first).toBeInViewport({ ratio: 1 });
  });
});

test("로그인 버튼은 맨 위와 소개 끝에 하나씩 선다", async ({ page }) => {
  await page.goto("/");

  await expect(page.getByRole("button", KAKAO)).toHaveCount(2);
});

test("🚨 예시가 일반 추천과 개인화 추천을 글자로 가른다 — 처음 며칠은 기록 0건의 일반 추천이다", async ({
  page,
}) => {
  await page.goto("/");
  const panel = page.getByRole("tabpanel");

  // 기본은 기록이 쌓인 뒤다 — 근거를 몇 건 썼는지 글자로 말한다.
  await expect(page.getByRole("tab", { name: "기록이 쌓이면" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  await expect(panel.getByText("사용한 기록 3건")).toBeVisible();
  await expect(panel.getByText("일반 추천", { exact: true })).toHaveCount(0);

  await page.getByRole("tab", { name: "처음 며칠" }).click();

  // 근거가 없을 때는 맞춤인 척하지 않는다 (최상위 CLAUDE.md §2). 라벨과 건수가 본체다.
  await expect(panel.getByText("일반 추천", { exact: true })).toBeVisible();
  await expect(panel.getByText("기록 0건")).toBeVisible();
  await expect(panel.getByText("사용한 기록", { exact: false })).toHaveCount(0);
});

test("🚨 화면 글자에 줄표와 이모지가 없다 — 예시 두 탭을 다 본다", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("button", KAKAO).first()).toBeEnabled();

  // `innerText` 는 지금 그려진 탭 패널만 담는다. 두 탭을 차례로 열어 각각 읽는다.
  const texts: string[] = [await page.locator("body").innerText()];
  await page.getByRole("tab", { name: "처음 며칠" }).click();
  await expect(page.getByRole("tabpanel").getByText("기록 0건")).toBeVisible();
  texts.push(await page.locator("body").innerText());

  for (const text of texts) {
    expect(text).not.toMatch(/[—–]/);
    expect(text).not.toMatch(/\p{Extended_Pictographic}/u);
  }
});
