import { CHILD, expect, recordApi, switchScenario, test } from "./fixtures";

/**
 * 로그인 유지 (#465 · docs/web/auth-consent-v1.md §2).
 *
 * 토큰은 메모리에만 있고 앱을 켤 때 refresh 쿠키로 되살린다. 목은 쿠키 대신 표식을 쓴다
 * (`mocks/handlers/auth.ts`). 새로고침이 "앱을 다시 켠 것" 이다 — 메모리가 비워진다.
 */

const HOME = new RegExp(`/child/${CHILD}/home$`);
const SETTINGS = `/child/${CHILD}/settings`;

test.describe("로그인한 채로", () => {
  test("🚨 / 를 열면 홈으로 간다 — 앱은 늘 / 로 열린다", async ({ page }) => {
    await page.goto("/");

    await expect(page).toHaveURL(HOME);
    await expect(page.getByRole("button", { name: "카카오로 시작하기" })).toHaveCount(0);
  });

  test("새로고침해도 로그인 화면으로 가지 않는다", async ({ page }) => {
    await page.goto(SETTINGS);
    await expect(page.getByRole("button", { name: "로그아웃" })).toBeVisible();

    await page.reload();

    await expect(page).toHaveURL(new RegExp(`${SETTINGS}$`));
    await expect(page.getByRole("button", { name: "로그아웃" })).toBeVisible();
  });

  test("🚨 로그아웃하면 다시 열어도 로그인 화면이다 — 이어 주던 쿠키가 지워졌다", async ({
    page,
  }) => {
    await page.goto(SETTINGS);
    await page.getByRole("button", { name: "로그아웃" }).click();
    await page.getByRole("dialog").getByRole("button", { name: "로그아웃" }).click();
    await expect(page).toHaveURL(/\/$/);

    await page.reload();

    await expect(page.getByRole("button", { name: "카카오로 시작하기" })).toBeVisible();
  });
});

test("🚨 로그인한 뒤에도 토큰은 어느 저장소에도 없다", async ({ page }) => {
  await page.goto(SETTINGS);
  await expect(page.getByRole("button", { name: "로그아웃" })).toBeVisible();

  const stored = await page.evaluate(() =>
    [localStorage, sessionStorage].flatMap((store) =>
      Object.keys(store).map((key) => `${key}=${store.getItem(key)}`),
    ),
  );

  // 목이 주는 access 토큰 (`mocks/fixtures.ts` 의 MOCK_TOKEN). 지난 판의 sessionStorage 키도 없어야 한다.
  expect(stored.filter((entry) => entry.includes("mock-token"))).toEqual([]);
  expect(stored.filter((entry) => entry.startsWith("icatch.session="))).toEqual([]);
});

test.describe("로그아웃이 서버에 닿지 못했을 때", () => {
  test.use({ scenario: "logout_unreached", allowFailedApi: true });

  test("🚨 나간 척하지 않는다 — 실패를 남기고, 다시 열어도 로그인돼 있다", async ({ page }) => {
    await page.goto(SETTINGS);
    await page.getByRole("button", { name: "로그아웃" }).click();
    await page.getByRole("dialog").getByRole("button", { name: "로그아웃" }).click();

    await expect(page.getByText("로그아웃하지 못했어요", { exact: false })).toBeVisible();
    await expect(page).toHaveURL(new RegExp(`${SETTINGS}$`));

    // 서버에 refresh 가 그대로라, 여기서 나간 척했다면 다시 켤 때 이렇게 로그인돼 있었을 것이다.
    await page.reload();
    await expect(page.getByRole("button", { name: "로그아웃" })).toBeVisible();
  });

  test("다시 누르면 나간다", async ({ page }) => {
    await page.goto(SETTINGS);
    await page.getByRole("button", { name: "로그아웃" }).click();
    const sheet = page.getByRole("dialog");
    await sheet.getByRole("button", { name: "로그아웃" }).click();
    await expect(sheet.getByText("로그아웃하지 못했어요", { exact: false })).toBeVisible();

    await sheet.getByRole("button", { name: "로그아웃" }).click();

    await expect(page).toHaveURL(/\/$/);
    await expect(page.getByRole("button", { name: "카카오로 시작하기" })).toBeVisible();
  });
});

test.describe("로그인 전", () => {
  test.use({ signedIn: false });

  test("/ 는 로그인 화면이다", async ({ page }) => {
    await page.goto("/");

    await expect(page.getByRole("button", { name: "카카오로 시작하기" })).toBeVisible();
  });
});

test.describe("access 가 만료된 채로 켰을 때", () => {
  test.use({ scenario: "session_expired" });

  test("첫 요청이 401 이어도 한 번 갱신하고 그대로 그린다", async ({ page }) => {
    const calls = recordApi(page);

    await page.goto(SETTINGS);

    await expect(page.getByRole("button", { name: "로그아웃" })).toBeVisible();
    await expect(page).toHaveURL(new RegExp(`${SETTINGS}$`));
    // 켤 때 한 번, 401 뒤 한 번. 🚨 쿼리 여러 개가 함께 401 을 받아도 갱신은 하나다.
    expect(calls.filter((c) => c.path === "/auth/refresh")).toHaveLength(2);
  });
});

test.describe("켤 때 서버에 닿지 못했을 때", () => {
  test.use({ scenario: "session_unreachable", allowFailedApi: true });

  test("🚨 로그인 화면으로 보내지 않고 연결 확인을 세운다 — 다시 시도하면 이어진다", async ({
    page,
  }) => {
    await page.goto(SETTINGS);

    await expect(page.getByText("로그인 상태를 확인하지 못했어요", { exact: false })).toBeVisible();
    await expect(page).toHaveURL(new RegExp(`${SETTINGS}$`));
    await expect(page.getByRole("button", { name: "카카오로 시작하기" })).toHaveCount(0);

    await switchScenario(page, "default");
    await page.getByRole("button", { name: "다시 시도" }).click();

    await expect(page.getByRole("button", { name: "로그아웃" })).toBeVisible();
  });
});
