import { CHILD, expect, test } from "./fixtures";

/**
 * 앱 셸의 알림 창구 (#609 FE-4) — 셸이 넘긴 "알림을 누른 화면" 을 웹이 어떻게 받는가.
 *
 * 실제 셸 대신 `window.icatch.push` 를 꽂고 `icatch:push` 를 쏜다 (`voice-input.spec.ts` 와 같은 방식).
 * 이 길은 웹 푸시 스위치(Firebase 값)와 상관없이 돈다 — e2e 는 그 값을 비운 채 돈다.
 *
 * 거는 것: 웹이 셸에 `ready` 를 보낸다(앱을 연 알림의 주소는 그 뒤에 온다) · 맞는 주소면 그 화면으로 옮긴다 ·
 * 모양이 다른 주소를 받은 뒤에도 흐름이 이어진다.
 * ⚠️ **주소 모양 검사 자체는 `src/lib/native/push.test.ts` 가 건다.** `router.push` 가 비동기라 이 e2e 의 "그대로다"
 *    확인은 이동이 끝나기 전에 통과할 수 있다 — 여기서는 화면까지 이어진 흐름만 본다.
 */
test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    const sent: string[] = [];
    (window as unknown as { __pushSent: string[] }).__pushSent = sent;
    (window as unknown as { icatch: unknown }).icatch = {
      push: {
        available: true,
        ready: () => sent.push("ready"),
        sync: () => sent.push("sync"),
        ask: () => sent.push("ask"),
      },
    };
  });
});

function sendOpen(page: import("@playwright/test").Page, path: string) {
  return page.evaluate((p) => {
    window.dispatchEvent(new CustomEvent("icatch:push", { detail: { type: "open", path: p } }));
  }, path);
}

test("🚨 웹이 뜨면 셸에 ready 를 보낸다 — 앱을 연 알림의 주소는 이 신호 뒤에 온다", async ({
  page,
}) => {
  await page.goto(`/child/${CHILD}/home`);
  await expect
    .poll(() => page.evaluate(() => (window as unknown as { __pushSent: string[] }).__pushSent))
    .toContain("ready");
});

test("셸이 넘긴 주소로 그날 09 를 연다", async ({ page }) => {
  await page.goto(`/child/${CHILD}/home`);
  await expect(page.getByRole("textbox", { name: "오늘 있었던 일" })).toBeVisible();

  await sendOpen(page, `/child/${CHILD}/calendar?date=2026-11-02`);
  await page.waitForURL(`**/child/${CHILD}/calendar?date=2026-11-02`);
});

test("모양이 다른 주소를 받은 뒤에도 맞는 주소로는 옮긴다", async ({ page }) => {
  await page.goto(`/child/${CHILD}/home`);
  await expect(page.getByRole("textbox", { name: "오늘 있었던 일" })).toBeVisible();

  await sendOpen(page, `/child/${CHILD}/settings/withdraw`);
  await sendOpen(page, "https://evil.example/");
  // 옮길 것이었다면 이 사이에 주소가 바뀐다. 맞는 주소 하나를 보내 이벤트가 처리되는 것을 확인하고 끝낸다.
  await expect(page).toHaveURL(new RegExp(`/child/${CHILD}/home$`));
  await sendOpen(page, `/child/${CHILD}/calendar?date=2026-11-02`);
  await page.waitForURL(`**/child/${CHILD}/calendar?date=2026-11-02`);
});
