import type { Locator, Page } from "@playwright/test";

import { CHILD, expect, recordApi, switchScenario, test, writes } from "./fixtures";
import { browserDaysAgo } from "./time-zone";

/**
 * 08 사진 확인 화면 — 서버가 읽은 날짜 칸을 화면이 그대로 쓰는가 (#723).
 *
 * 목의 알림장(`mocks/handlers/photos.ts` 의 `noticeEntries`)은 서버 모양을 흉내 낸다 —
 * 시각을 읽은 일정(도서관 나들이 9:30), 여러 날 일정(독서 주간), 마감만 있는 할 일(동의서 회신),
 * 날짜 없는 안내(비 오는 날 실내 활동), 시각 있는 시작에 하루 종일 마감이 붙은 일정(부모 참여 수업),
 * 시각 있는 마감(가정통신문 회신)이 한 장에 있다.
 */

async function openNotice(page: Page, scenario?: "photo_meal_plan"): Promise<void> {
  await page.goto(`/child/${CHILD}/home`);
  if (scenario) await switchScenario(page, scenario);
  await page.getByRole("button", { name: "사진으로 적기" }).click();
  await page.getByRole("button", { name: /^알림장·식단표/ }).click();
  await page
    .getByRole("button", { name: /기기에 있는 최근 사진/ })
    .first()
    .click();
  await expect(page.getByText("알림장이나 식단표로 읽었어요.")).toBeVisible({ timeout: 15_000 });
  await page.getByRole("button", { name: /^잘 읽었어요/ }).click();
}

function row(page: Page, title: string): Locator {
  return page.getByRole("listitem").filter({ hasText: title });
}

async function openSheet(page: Page, title: string): Promise<Locator> {
  await row(page, title)
    .getByRole("button", { name: /^고치기/ })
    .click();
  return page.getByRole("dialog");
}

test("목록 — 여러 날은 기간, 할 일은 마감, 날짜 없는 안내에는 날짜 줄이 없다", async ({ page }) => {
  await openNotice(page);

  await expect(row(page, "독서 주간")).toContainText(/언제.*부터.*까지/);
  // 🚨 읽은 시각은 오전 · 오후와 함께 보인다 — 표시 없는 시각을 오전으로 읽은 것을 보호자가 확인한다.
  await expect(row(page, "도서관 나들이")).toContainText("오전 9:30");
  await expect(row(page, "체험학습 동의서 회신")).toContainText("마감");
  await expect(row(page, "체험학습 동의서 회신")).not.toContainText("언제");

  // 🚨 안내는 날짜가 없는 것이 정상이다. 못 읽은 값처럼 보이면 보호자가 없던 날을 고치러 간다.
  const info = row(page, "비 오는 날 실내 활동");
  await expect(info).not.toContainText("못 읽었어요");
  await expect(info).not.toContainText("언제");
  // 일정 칸이 없으니 버튼도 일정을 약속하지 않는다.
  await expect(info.getByRole("button")).toHaveText("고치기");

  // 날짜를 정말 못 읽은 일정은 그대로 말한다.
  await expect(row(page, "생일잔치")).toContainText("못 읽었어요");
});

test("읽은 시각이 시각 칸에 채워져 열리고, 그대로 넣을 수 있다", async ({ page }) => {
  const calls = recordApi(page);
  await openNotice(page);
  const sheet = await openSheet(page, "도서관 나들이");

  await sheet.getByText("일정으로도 넣기", { exact: true }).click();
  await expect(sheet.getByRole("combobox", { name: "시작 시간 — 시" })).toHaveText(/오전 9시/);
  await expect(sheet.getByRole("combobox", { name: "시작 시간 — 분" })).toHaveText(/30분/);

  await sheet.getByRole("button", { name: "확인했어요, 캘린더에 넣을게요" }).click();
  await expect(sheet.getByText(/캘린더에 넣었어요/)).toBeVisible();

  const [submitted] = writes(calls, "POST", /\/children\/c1\/events$/);
  expect(submitted.body).toMatchObject({
    event: {
      starts_at: `${browserDaysAgo(-5)}T09:30:00+09:00`,
      ends_at: null,
      all_day: false,
    },
  });
});

test("여러 날 일정은 끝날까지 함께 넣고, 시작을 고치면 그날 하루로 줄었다고 말한다", async ({
  page,
}) => {
  const calls = recordApi(page);
  await openNotice(page);
  const sheet = await openSheet(page, "독서 주간");

  await sheet.getByText("일정으로도 넣기", { exact: true }).click();
  // 🚨 함께 넣는 끝은 게이트 버튼 앞에서 보인다.
  await expect(sheet.getByText(/부터 .*까지 함께 넣어요/)).toBeVisible();

  // 하루 종일을 끄면 읽은 기간이 아니다 — 조용히 줄이지 않고 말한다.
  // 시작 시각을 고르기 전에는 잠긴 이유가 먼저 서므로, 시각까지 고른 뒤에 본다.
  await sheet.getByText("하루 종일", { exact: true }).click();
  await expect(sheet.getByText(/그날 하루로 넣어요/)).toHaveCount(0);
  await sheet.getByRole("combobox", { name: "시작 시간 — 시" }).click();
  await page.getByRole("option", { name: "오전 9시", exact: true }).click();
  await sheet.getByRole("combobox", { name: "시작 시간 — 분" }).click();
  await page.getByRole("option", { name: "00분", exact: true }).click();
  await expect(sheet.getByText(/그날 하루로 넣어요/)).toBeVisible();
  await sheet.getByText("하루 종일", { exact: true }).click();
  await expect(sheet.getByText(/부터 .*까지 함께 넣어요/)).toBeVisible();

  await sheet.getByRole("button", { name: "확인했어요, 캘린더에 넣을게요" }).click();
  await expect(sheet.getByText(/캘린더에 넣었어요\. .*부터 .*까지/)).toBeVisible();

  const [submitted] = writes(calls, "POST", /\/children\/c1\/events$/);
  expect(submitted.body).toMatchObject({
    event: {
      starts_at: `${browserDaysAgo(-8)}T00:00:00+09:00`,
      ends_at: `${browserDaysAgo(-12)}T23:59:00+09:00`,
      all_day: true,
    },
  });
});

test("🚨 할 일의 마감일을 일정의 시작으로 쓰지 않는다 — 시작하는 날을 따로 고른다", async ({
  page,
}) => {
  const calls = recordApi(page);
  await openNotice(page);
  const sheet = await openSheet(page, "체험학습 동의서 회신");

  // 날짜 칸의 달력(닫힌 시트)도 같은 이름을 제목으로 들고 있어서 첫 번째(칸의 이름)만 본다.
  await expect(sheet.getByText("마감", { exact: true }).first()).toBeVisible();
  await expect(sheet.getByText("언제", { exact: true })).toHaveCount(0);
  await sheet.getByText("일정으로도 넣기", { exact: true }).click();

  await expect(sheet.getByText("시작하는 날", { exact: true }).first()).toBeVisible();
  await expect(sheet.getByText("시작하는 날을 골라주셔야 넣을 수 있어요.")).toBeVisible();
  await expect(sheet.getByRole("button", { name: "확인했어요, 캘린더에 넣을게요" })).toBeDisabled();
  expect(writes(calls, "POST", /\/children\/c1\/events$/)).toHaveLength(0);
});

test("안내에는 일정으로 넣는 칸이 없다", async ({ page }) => {
  await openNotice(page);
  const sheet = await openSheet(page, "비 오는 날 실내 활동");

  await expect(sheet.getByText("날짜가 없는 안내면 비워 두세요.")).toBeVisible();
  await expect(sheet.getByText("일정으로도 넣기", { exact: true })).toHaveCount(0);
});

test("넣은 일정의 id 가 저장할 때 그 항목에 실린다", async ({ page }) => {
  const calls = recordApi(page);
  await openNotice(page);
  const sheet = await openSheet(page, "도서관 나들이");

  const created = page.waitForResponse(
    (res) => res.request().method() === "POST" && /\/children\/c1\/events$/.test(res.url()),
  );
  await sheet.getByText("일정으로도 넣기", { exact: true }).click();
  await sheet.getByRole("button", { name: "확인했어요, 캘린더에 넣을게요" }).click();
  const eventId = ((await (await created).json()) as { event: { id: string } }).event.id;
  await expect(sheet.getByText(/캘린더에 넣었어요/)).toBeVisible();
  await page.keyboard.press("Escape");

  await page.getByRole("button", { name: "이 내용으로 저장" }).click();
  await page.waitForURL(`**/child/${CHILD}/home`);

  const [commit] = writes(calls, "POST", /\/photo-runs\/[^/]+\/commit$/);
  const entries = (commit.body as { entries: Array<{ id: string; event_id: string | null }> })
    .entries;
  expect(entries.find((e) => e.id === "pe_1")?.event_id).toBe(eventId);
  expect(entries.filter((e) => e.id !== "pe_1").every((e) => e.event_id === null)).toBe(true);
});

test("🚨 급식 날짜는 마감이 아니다 — 목록도 시트도 언제다 (#792 리뷰)", async ({ page }) => {
  await openNotice(page, "photo_meal_plan");

  const meal = page.getByRole("listitem").filter({ hasText: "급식" }).first();
  await expect(meal).toContainText("언제");
  await expect(meal).not.toContainText("마감");

  // 같은 메뉴가 여러 날 되풀이되어 제목으로는 한 줄을 못 집는다. 첫 급식 줄을 연다.
  await meal.getByRole("button", { name: /^고치기/ }).click();
  const sheet = page.getByRole("dialog");
  await expect(sheet.getByText("언제", { exact: true }).first()).toBeVisible();
  await expect(sheet.getByText("마감", { exact: true })).toHaveCount(0);
});

test("시각을 읽은 시작은 하루 종일 마감이 붙어도 시각과 끝을 그대로 쓴다 (#792 리뷰)", async ({
  page,
}) => {
  await openNotice(page);
  await expect(row(page, "부모 참여 수업")).toContainText("오전 10:00");

  const sheet = await openSheet(page, "부모 참여 수업");
  await sheet.getByText("일정으로도 넣기", { exact: true }).click();
  await expect(sheet.getByRole("combobox", { name: "시작 시간 — 시" })).toHaveText(/오전 10시/);
  // 🚨 함께 넣는 끝은 게이트 앞에서 보인다.
  await expect(sheet.getByText(/오후 12:00에 끝나요/)).toBeVisible();

  // 시작을 끝보다 늦게 고치면 끝이 빠지고, 빠졌다고 말한다.
  await sheet.getByRole("combobox", { name: "시작 시간 — 시" }).click();
  await page.getByRole("option", { name: "오후 1시", exact: true }).click();
  await expect(
    sheet.getByText("시작이나 하루 종일을 바꿔서 읽은 끝나는 시각 없이 넣어요."),
  ).toBeVisible();
  await expect(sheet.getByText(/에 끝나요/)).toHaveCount(0);
});

test("시각을 읽은 마감은 목록에 시각까지 보이고, 일정의 시작은 하루 종일에서 연다", async ({
  page,
}) => {
  await openNotice(page);
  await expect(row(page, "가정통신문 회신")).toContainText(/마감.*오후 6:00/);

  const sheet = await openSheet(page, "가정통신문 회신");
  await sheet.getByText("일정으로도 넣기", { exact: true }).click();
  // 🚨 마감의 하루 종일 여부는 일정의 시작과 상관없다. 마감 시각을 시작 시각으로 쓰지 않는다.
  await expect(sheet.getByRole("checkbox", { name: "하루 종일" })).toBeChecked();
  await expect(sheet.getByRole("group", { name: "시작 시간" })).toHaveCount(0);
});
