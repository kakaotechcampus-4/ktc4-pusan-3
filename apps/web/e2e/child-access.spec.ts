import { asOtherCaregiver, CHILD, expect, recordApi, test } from "./fixtures";

/**
 * 내 아이가 아닌 아이 주소로 들어올 때 (#802 · `components/child-scope.tsx`).
 *
 * 서버는 없는 아이와 남의 아이를 같은 403 으로 막는다. 화면도 둘을 가르지 않고, `/me` 목록에 없으면
 * 화면째 "볼 수 없는 아이" 를 세운다 — 구역마다 실패 카드가 서는 것이 아니다.
 * 403 을 받으면 `/me` 를 다시 받는 것 자체는 `lib/query-client.test.ts` 와 `stores/conversation-lost-child.test.ts` 가 건다.
 */

const NOT_MINE = [
  { name: "UUID 모양의 다른 아이", id: "0b7f3c1e-4d2a-4f6b-9c8e-1a2b3c4d5e6f", screen: "home" },
  { name: "UUID 모양이 아닌 값", id: "not-a-child", screen: "calendar" },
];

for (const { name, id, screen } of NOT_MINE) {
  test(`${name} — 볼 수 없는 아이 화면이 서고, 그 아이로는 아무것도 부르지 않는다`, async ({
    page,
  }) => {
    const calls = recordApi(page);
    await page.goto(`/child/${id}/${screen}`);

    await expect(page.getByRole("heading", { name: "볼 수 없는 아이예요" })).toBeVisible();
    await expect(
      page.getByText("주소가 잘못됐거나, 지금은 함께 보고 있지 않은 아이예요."),
    ).toBeVisible();
    // 🚨 판정이 끝나기 전에 화면을 그리지 않는다 — 그 id 로 나간 요청이 없어야 한다.
    expect(calls.filter((call) => call.path.startsWith(`/children/${id}`))).toEqual([]);
    // 화면째 바뀐 것이라 하단 네비도 없다 (다른 칸을 누르면 같은 아이 주소로 간다).
    await expect(page.getByRole("navigation")).toHaveCount(0);

    // 🚨 가는 곳은 URL 이 아니라 `/me` 목록에서 만든다.
    await page.getByRole("link", { name: "내 아이 홈으로" }).click();
    await expect(page).toHaveURL(new RegExp(`/child/${CHILD}/home$`));
  });
}

test("🚨 막힌 아이는 마지막에 본 아이로 남지 않는다", async ({ page }) => {
  await page.goto(`/child/${CHILD}/settings`);
  await expect(page.getByRole("button", { name: "로그아웃" })).toBeVisible();

  await page.goto("/child/not-a-child/home");
  await expect(page.getByRole("heading", { name: "볼 수 없는 아이예요" })).toBeVisible();

  // `stores/session.ts` 의 persist 값. 확인된 아이만 들어간다.
  const stored = await page.evaluate(() => localStorage.getItem("icatch.active-child"));
  expect(JSON.parse(stored ?? "{}").state?.activeChildId).toBe(CHILD);
});

test.describe("로그인하지 않은 채로", () => {
  test.use({ signedIn: false });

  test("🚨 판정하지 않고 로그인 화면으로 보낸다", async ({ page }) => {
    await page.goto("/child/not-a-child/home");

    await expect(page.getByRole("button", { name: "카카오로 시작하기" }).first()).toBeVisible();
    await expect(page.getByRole("heading", { name: "볼 수 없는 아이예요" })).toHaveCount(0);
  });
});

test.describe("아이가 0명인 계정", () => {
  test.use({ scenario: "consent" });

  test("처음 화면으로 보낸다 — 거기서 초대 코드 입력과 아이 만들기를 고른다", async ({ page }) => {
    await page.goto(`/child/${CHILD}/home`);

    await expect(page.getByRole("heading", { name: "볼 수 없는 아이예요" })).toBeVisible();
    await page.getByRole("link", { name: "처음 화면으로" }).click();
    await expect(page).toHaveURL(/\/start$/);
  });

  /**
   * 🚨 **보는 중에 연결이 끊기면 화면째 바뀐다.** 초대로 연결한 뒤 다른 보호자가 연결을 끊고, 다음 화면의
   *    요청이 403 `child_access_denied` 를 받는다. 그 403 이 `/me` 를 다시 받게 하고, 판정이 다시 돈다.
   *    🚨 목 상태는 탭 안에만 산다 — 초대 수락부터 `page.goto` 없이 이어 간다.
   */
  test("🚨 보는 중에 연결이 끊기면 다음 요청의 403 에서 볼 수 없는 아이로 바뀐다", async ({
    page,
  }) => {
    await page.goto("/invite");
    await page.getByLabel("초대 코드").fill("MKGRAND1");
    await page.getByRole("button", { name: "코드 확인하기" }).click();
    await page.getByText("만 19세 이상이에요").click();
    await page.getByRole("button", { name: "연결하기" }).click();
    await expect(page).toHaveURL(/\/child\/[^/]+\/home$/);
    const childId = new URL(page.url()).pathname.split("/")[2]!;

    expect(
      await asOtherCaregiver(page, { method: "DELETE", path: `/children/${childId}/parents/p1` }),
    ).toBe(204);
    // 설정은 보호자 목록을 처음 받는다 — 그 요청이 403 이다.
    await page.getByRole("button", { name: "설정" }).click();

    await expect(page.getByRole("heading", { name: "볼 수 없는 아이예요" })).toBeVisible();
    await expect(page.getByRole("link", { name: "처음 화면으로" })).toBeVisible();
  });
});
