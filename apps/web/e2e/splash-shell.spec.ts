import { CHILD, expect, test } from "./fixtures";

/**
 * 앱 셸의 스플래시 창구 (#690) — 웹이 첫 화면을 세웠을 때만 셸에 알리는가.
 *
 * 실제 셸 대신 `window.icatch.splash` 를 꽂는다 (`push-shell.spec.ts` 와 같은 방식). 알릴 때의 주소와
 * 화면 글자를 같이 적어 둔다 — 셸은 이 신호에 스플래시 덮개를 걷으므로, 그 순간 보이는 것이 곧 보호자가 보는 첫 화면이다.
 *
 * ⚠️ 한 번만 보내는 것 자체는 `src/lib/native/splash.test.ts` 가 건다. 여기서는 **어느 화면이 보내는지**를 본다.
 */
test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    const sent: Array<{ path: string; checking: boolean; loginDisabled: boolean | null }> = [];
    (window as unknown as { __splashSent: typeof sent }).__splashSent = sent;
    (window as unknown as { icatch: unknown }).icatch = {
      splash: {
        done: () =>
          sent.push({
            path: location.pathname,
            checking: document.body.innerText.includes("불러오는 중"),
            // 00 로그인의 카카오 버튼. 없으면 null (로그인한 채로 켠 경우).
            loginDisabled:
              Array.from(document.querySelectorAll("button"))
                .find((button) => button.textContent?.includes("카카오로 시작하기"))
                ?.hasAttribute("disabled") ?? null,
          }),
      },
    };
  });
});

function splashSent(page: import("@playwright/test").Page) {
  return page.evaluate(
    () =>
      (
        window as unknown as {
          __splashSent: Array<{ path: string; checking: boolean; loginDisabled: boolean | null }>;
        }
      ).__splashSent,
  );
}

test("🚨 로그인한 채로 앱을 켜면 세션 확인 화면이 아니라 홈이 선 뒤에 알린다", async ({ page }) => {
  // 앱은 늘 / 로 열린다 — 세션을 되살리는 동안 "불러오는 중…" 이 서고, 그 뒤 홈으로 옮긴다.
  await page.goto("/");
  await expect(page).toHaveURL(new RegExp(`/child/${CHILD}/home$`));

  await expect.poll(() => splashSent(page)).toHaveLength(1);
  expect(await splashSent(page)).toEqual([
    { path: `/child/${CHILD}/home`, checking: false, loginDisabled: null },
  ]);
});

test("화면을 옮겨도 다시 알리지 않는다 — 셸에 보낼 것은 문서마다 처음 한 번이다", async ({
  page,
}) => {
  await page.goto(`/child/${CHILD}/home`);
  await expect.poll(() => splashSent(page)).toHaveLength(1);

  await page.getByRole("link", { name: "캘린더" }).click();
  await page.waitForURL(`**/child/${CHILD}/calendar**`);

  expect(await splashSent(page)).toHaveLength(1);
});

test.describe("로그인하지 않은 채로", () => {
  test.use({ signedIn: false });

  test("🚨 로그인 버튼이 켜진 뒤에 알린다 — 꺼진 회색 버튼이 스플래시 뒤에서 드러나지 않는다", async ({
    page,
  }) => {
    await page.goto("/");
    await expect(page.getByRole("button", { name: "카카오로 시작하기" })).toBeEnabled();

    await expect.poll(() => splashSent(page)).toHaveLength(1);
    expect(await splashSent(page)).toEqual([{ path: "/", checking: false, loginDisabled: false }]);
  });
});
