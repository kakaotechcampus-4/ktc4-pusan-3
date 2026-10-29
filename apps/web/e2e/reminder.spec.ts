import type { Locator, Page } from "@playwright/test";

import type { CalendarDayResponse } from "@/lib/api/types";

import { CHILD, expect, expectNoGate, recordApi, test, writes } from "./fixtures";
import { browserDaysAgo } from "./time-zone";

/**
 * 일정 알림 (#609 · 서버 #603) — 알림은 **보호자 개인 설정**이다.
 *
 * - 새 일정 초안에서 고른 알림은 제출 본문의 `reminder` 로 같이 간다 (기본값 "없음" = `null`)
 * - 🚨 수정 초안은 `reminder` 를 아예 싣지 않는다 — 칸이 없어야 서버가 걸린 알림을 그대로 둔다
 * - 09 하루 패널에서 다른 보호자가 각자 건다. 키 없는 `PUT` · `DELETE` 이고 승인 게이트가 아니다
 *
 * 목의 초안은 04 대화의 "지어낸 한 줄" 이 내는 두 장이다 — 1번째가 새 일정(사흘 뒤), 2번째가 수정이다.
 */

async function openRunDrafts(page: Page): Promise<Locator> {
  await page.goto(`/child/${CHILD}/home`);
  await page.getByRole("textbox", { name: "오늘 있었던 일" }).fill("지어낸 한 줄");
  await page.getByRole("button", { name: "이 이야기 남기기" }).click();
  await page.waitForURL(`**/child/${CHILD}/chat`);
  await page
    .getByRole("listitem")
    .filter({ hasText: "지어낸 한 줄" })
    .first()
    .getByRole("button", { name: "확인하고 넣기" })
    .click();
  const sheet = page.getByRole("dialog", { name: "찾은 일정" });
  await expect(sheet.getByText("2건 중 1번째")).toBeVisible();
  return sheet;
}

async function pickReminder(scope: Locator, page: Page, label: string) {
  await scope.getByRole("combobox", { name: "알림" }).click();
  await page.getByRole("option", { name: label, exact: true }).click();
}

test.describe("초안 카드의 알림 칸", () => {
  test("고른 알림이 제출 본문에 실리고, 넣은 카드가 그 알림을 말한다", async ({ page }) => {
    const calls = recordApi(page);
    const sheet = await openRunDrafts(page);
    const reminder = sheet.getByRole("combobox", { name: "알림" });

    // 🚨 기본값은 "없음" 이다. 알림은 나에게만 울린다는 것을 칸이 먼저 말한다.
    await expect(reminder).toHaveText(/없음/);
    await expect(reminder).toHaveAccessibleDescription("나에게만 울려요.");

    await pickReminder(sheet, page, "1시간 전");
    await sheet.getByRole("button", { name: "확인했어요, 캘린더에 넣을게요" }).click();
    await expect(sheet.getByText("알림 1시간 전")).toBeVisible();

    const submitted = writes(calls, "POST", /\/children\/c1\/events$/);
    expect(submitted).toHaveLength(1);
    expect(submitted[0].body).toMatchObject({ reminder: { lead: "1h" } });
  });

  test("안 고르면 reminder 가 null 로 간다", async ({ page }) => {
    const calls = recordApi(page);
    const sheet = await openRunDrafts(page);

    await sheet.getByRole("button", { name: "확인했어요, 캘린더에 넣을게요" }).click();
    await expect(sheet.getByText("2건 중 1건 넣었어요.")).toBeVisible();

    expect(writes(calls, "POST", /\/children\/c1\/events$/)[0].body).toMatchObject({
      reminder: null,
    });
  });

  test("🚨 하루 종일을 켜면 고른 알림을 지우지 않고 오전 9시 목록의 값으로 옮긴다", async ({
    page,
  }) => {
    const sheet = await openRunDrafts(page);
    const reminder = sheet.getByRole("combobox", { name: "알림" });

    await pickReminder(sheet, page, "1일 전");
    await sheet.getByText("하루 종일", { exact: true }).click();
    await expect(reminder).toHaveText(/1일 전 오전 9시/);

    // 종일 일정의 목록에는 "몇 분 · 몇 시간 전" 이 없다.
    // 🚨 "1주 전 오전 9시" 도 없다 — 이 초안은 사흘 뒤라 그 시각이 이미 지났다 (지난 값은 목록에서 빠진다).
    await reminder.click();
    await expect(page.getByRole("option")).toHaveText([
      "없음",
      "당일 오전 9시",
      "1일 전 오전 9시",
      "2일 전 오전 9시",
    ]);
    await page.keyboard.press("Escape");

    // 다시 끄면 같은 날 수의 값으로 돌아온다.
    await sheet.getByText("하루 종일", { exact: true }).click();
    await expect(reminder).toHaveText(/1일 전/);
    await expect(reminder).not.toHaveText(/오전 9시/);
  });

  test("🚨 수정 초안에는 알림 칸이 없고, PATCH 본문에 reminder 칸 자체가 없다", async ({
    page,
  }) => {
    const calls = recordApi(page);
    const sheet = await openRunDrafts(page);

    await sheet.getByRole("button", { name: "다음", exact: true }).click();
    await expect(sheet.getByText("2건 중 2번째")).toBeVisible();
    await expect(sheet.getByRole("combobox", { name: "알림" })).toHaveCount(0);

    await sheet.getByRole("button", { name: "확인했어요, 캘린더에 넣을게요" }).click();
    await expect.poll(() => writes(calls, "PATCH", /\/events\//)).toHaveLength(1);
    const body = writes(calls, "PATCH", /\/events\//)[0].body as Record<string, unknown>;
    expect(Object.keys(body)).not.toContain("reminder");
  });
});

test("09 하루 패널에서 내 알림을 고르면 키 없이 저장되고, 없음으로 바꾸면 지운다", async ({
  page,
}) => {
  // 목의 저장된 일정은 이틀 뒤 오전 10시다.
  const date = browserDaysAgo(-2);
  const dayPath = `/children/${CHILD}/calendar/${date}`;
  const reminderPath = `/children/${CHILD}/events/e_3/reminder`;
  const calls = recordApi(page);
  await page.goto(`/child/${CHILD}/calendar?date=${date}`);

  const reminder = page.getByRole("combobox", { name: "알림" });
  await expect(reminder).toHaveText(/없음/);
  // 🚨 하루에 일정이 여럿이면 칸 이름이 전부 "알림" 이다 — 접근용 이름은 일정 제목으로 시작한다 (#611 리뷰).
  await expect(page.getByRole("combobox", { name: /^물놀이 알림/ })).toBeVisible();

  const reread = page.waitForResponse(
    (res) =>
      res.request().method() === "GET" && new URL(res.url()).pathname === `/api/v1${dayPath}`,
  );
  await pickReminder(page.locator("body"), page, "30분 전");

  // 🚨 되돌릴 수 있는 개인 설정이라 Idempotency-Key 가 없다.
  await expect
    .poll(() => writes(calls, "PUT", /\/reminder$/))
    .toEqual([
      { method: "PUT", path: reminderPath, body: { lead: "30m" }, idempotencyKey: undefined },
    ]);
  // 다시 받은 하루 보기에도 남는다 — 화면의 값이 낙관적 표시만이 아니다.
  const day = (await (await reread).json()) as CalendarDayResponse;
  expect(day.events[0]?.reminder).toEqual({ lead: "30m" });
  await expect(reminder).toHaveText(/30분 전/);

  await pickReminder(page.locator("body"), page, "없음");
  await expect.poll(() => writes(calls, "DELETE", /\/reminder$/)).toHaveLength(1);
  await expect(reminder).toHaveText(/없음/);

  // 승인 게이트가 아니다 (§2 실행 — 게이트를 늘리지 않는다).
  await expectNoGate(page);
});
