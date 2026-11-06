import type { Page } from "@playwright/test";

import { CHILD, expect, gateMarks, recordApi, test, writes } from "./fixtures";
import { browserDaysAgo } from "./time-zone";

/**
 * 요일 반복 일정 (#738) — 최상위 §2 · §3 이 화면에서 지켜지는가.
 *
 * - 회차 날짜는 서버 규칙이 정한다 — 화면은 반복 문구를 그대로 그리고 첫 회차 날짜를 고치지 않는다.
 * - 반복 묶음 고치기도 승인 게이트 ㉠ 이다 — 누르기 전에는 아무것도 쓰지 않고, 키를 단다.
 * - 묶음 고치기는 읽기 전용이고, 범위(오늘 이후 회차 전부)를 먼저 말한다.
 * 🚨 지어낸 일정이다 (최상위 §9).
 */

async function openDrafts(page: Page) {
  const text = "지어낸 반복 한 줄";
  await page.goto(`/child/${CHILD}/home`);
  await page.getByRole("textbox", { name: "오늘 있었던 일" }).fill(text);
  await page.getByRole("button", { name: "이 이야기 남기기" }).click();
  await page.waitForURL(`**/child/${CHILD}/chat`);
  await page
    .getByRole("listitem")
    .filter({ hasText: text })
    .first()
    .getByRole("button", { name: "확인하고 넣기" })
    .click();
  return page.getByRole("dialog", { name: "찾은 일정" });
}

test.describe("event_repeat — 반복 일정 초안", () => {
  test.use({ scenario: "event_repeat" });

  test("🚨 새 반복 일정은 반복 문구를 그리고 날짜를 고칠 칸이 없다 · 요일과 끝 날짜를 싣고 문구는 싣지 않는다", async ({
    page,
  }) => {
    const calls = recordApi(page);
    const sheet = await openDrafts(page);

    await expect(sheet.getByText("2건 중 1번째")).toBeVisible();
    await expect(sheet.getByText("새 반복 일정").filter({ visible: true })).toBeVisible();
    await expect(sheet.getByText(/까지 매주 월, 수$/).filter({ visible: true })).toBeVisible();
    await expect(sheet.getByText(/^첫 회차는 /).filter({ visible: true })).toBeVisible();
    // 🚨 날짜를 고르는 칸이 서면 첫 회차를 요일 밖으로 옮길 수 있다 — 화면은 그걸 알 수 없다.
    await expect(
      sheet.getByRole("button", { name: /^언제/ }).filter({ visible: true }),
    ).toHaveCount(0);
    // 시각은 회차마다 같아서 고칠 수 있다.
    await expect(
      sheet.getByRole("group", { name: "시작 시간" }).filter({ visible: true }),
    ).toBeVisible();

    expect(writes(calls, "POST", /\/events$/)).toEqual([]);
    await expect
      .poll(() => gateMarks(page, "dialog[open]"))
      .toEqual(
        expect.arrayContaining([
          "approve 버튼: 확인했어요, 캘린더에 넣을게요",
          expect.stringMatching(/^caution 색: 누르면 반복하는 날마다 캘린더에 들어가요/),
        ]),
      );

    await sheet.getByRole("button", { name: "확인했어요, 캘린더에 넣을게요" }).click();
    await expect(sheet.getByText("넣었어요", { exact: true })).toBeVisible();

    const submitted = writes(calls, "POST", /\/children\/c1\/events$/);
    expect(submitted).toHaveLength(1);
    expect(submitted[0].idempotencyKey).toBeTruthy();
    const body = submitted[0].body as { repeat?: Record<string, unknown> };
    // 🚨 빠지면 하루짜리 일정 한 건이 된다.
    expect(body.repeat).toEqual({ weekdays: [1, 3], until: expect.any(String) });
  });

  test("🚨 묶음 고치기는 범위를 먼저 말하고 읽기 전용이다 · 누르기 전에는 쓰지 않고 before 를 실어 PATCH 한다", async ({
    page,
  }) => {
    const calls = recordApi(page);
    const sheet = await openDrafts(page);
    await sheet.getByRole("button", { name: "다음" }).click();
    await expect(sheet.getByText("2건 중 2번째")).toBeVisible();

    const card = sheet.locator("div:not([hidden]) > *").filter({ hasText: "반복 일정 수정" });
    await expect(
      card.getByText("오늘 이후 회차가 모두 바뀌어요. 지난 회차는 그대로예요."),
    ).toBeVisible();
    // 무엇이 달라지는지 — 시각과 준비물 (서버가 준 before 와 비교).
    await expect(card.getByText("이렇게 달라져요")).toBeVisible();
    await expect(card.getByText("오후 3:00", { exact: true })).toBeVisible();
    await expect(card.getByText("물안경", { exact: true })).toBeVisible();
    // 준비물은 고칠 칸처럼 보이지 않게 이름을 이은 한 줄이다.
    await expect(card.getByText("수영복, 수건, 물안경", { exact: true })).toBeVisible();
    // 🚨 읽기 전용 — 고칠 칸이 없다.
    await expect(card.getByRole("textbox")).toHaveCount(0);

    expect(writes(calls, "PATCH")).toEqual([]);
    await expect
      .poll(() => gateMarks(page, "dialog[open]"))
      .toEqual(expect.arrayContaining(["approve 버튼: 확인했어요, 앞으로 전부 바꿀게요"]));

    await card.getByRole("button", { name: "확인했어요, 앞으로 전부 바꿀게요" }).click();
    await expect(card.getByText("바꿨어요", { exact: true })).toBeVisible();
    // 🚨 고친 것을 "넣었다" 고 말하지 않는다.
    await expect(sheet.getByText("2건 중 1건 마쳤어요.")).toBeVisible();

    const patched = writes(calls, "PATCH", /\/children\/c1\/event-series\/series_1$/);
    expect(patched).toHaveLength(1);
    expect(patched[0].idempotencyKey).toBeTruthy();
    const body = patched[0].body as { before: unknown; items: string[] };
    expect(body.before).not.toBeNull();
    expect(body.items).toEqual(["수영복", "수건", "물안경"]);
    // 🚨 화면 전용 키 · 서버 문구는 싣지 않는다.
    expect(JSON.stringify(body)).not.toMatch(/draft_id|label|series_id/);
  });
});

test.describe("event_repeat — 09 캘린더", () => {
  test.use({ scenario: "event_repeat" });

  test("반복 묶음의 회차에 그 반복 문구가 붙는다", async ({ page }) => {
    await page.goto(`/child/${CHILD}/calendar?date=${browserDaysAgo(-2)}`);
    await expect(page.getByText("물놀이", { exact: true })).toBeVisible();
    await expect(page.getByText(/까지 매주 월, 수$/)).toBeVisible();
  });
});
