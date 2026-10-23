import type { Page } from "@playwright/test";

import { CHILD, expect, gateMarks, recordApi, test, writes } from "./fixtures";

/**
 * 복약 (#537) — 최상위 §2 가 화면에서 지켜지는가.
 *
 * - 복약 제출은 승인 게이트 ㉡ 다 — 넣기 전에는 아무것도 쓰지 않고, 비어 온 기간을 채우기 전에는 넣을 수 없다.
 * - 회차 체크는 게이트가 아니다 — 게이트 표식이 없다.
 * - 서류는 body 그대로 + 복사, 응급 카드의 조회 실패 줄은 그대로다.
 * 🚨 지어낸 약 이름이다 (최상위 §9).
 */

async function sendFromHome(page: Page, text: string) {
  await page.goto(`/child/${CHILD}/home`);
  await page.getByRole("textbox", { name: "오늘 있었던 일" }).fill(text);
  await page.getByRole("button", { name: "이 이야기 남기기" }).click();
  await page.waitForURL(`**/child/${CHILD}/chat`);
}

function turn(page: Page, text: string) {
  return page.getByRole("listitem").filter({ hasText: text }).first();
}

test.describe("med_draft — 복약 초안 (게이트 ㉡)", () => {
  test.use({ scenario: "med_draft" });

  test("🚨 알림 시각을 보여 주고, 비어 온 기간을 고르기 전에는 넣을 수 없고, 누르기 전에는 쓰지 않는다", async ({
    page,
  }) => {
    const calls = recordApi(page);
    await sendFromHome(page, "기침 시럽 하루 세 번 식후");
    const reply = turn(page, "기침 시럽 하루 세 번 식후");

    await expect(reply.getByText("적어주신 말에서 복약 2건을 찾았어요.")).toBeVisible();
    await reply.getByRole("button", { name: "확인하고 넣기" }).click();

    const sheet = page.getByRole("dialog", { name: "찾은 복약" });
    await expect(sheet.getByText("2건 중 1번째")).toBeVisible();
    // 🚨 보호자가 승인하는 것은 실제로 울릴 시각이다.
    // 숨은 장도 마운트돼 있어서(넘겨도 제출 상태가 남게) 보이는 것만 본다.
    await expect(sheet.getByText("08:30, 12:30, 19:30").filter({ visible: true })).toBeVisible();
    await expect(sheet.getByText("아직 저장되지 않았어요").filter({ visible: true })).toBeVisible();

    // 🚨 기간이 비어 왔다 — 오늘로 채우지 않고 잠근다.
    const submit = sheet.getByRole("button", { name: "확인했어요, 복약 일정에 넣을게요" });
    await expect(submit).toBeDisabled();
    await expect(sheet.getByText(/언제까지 먹이는지 골라주셔야 넣을 수 있어요/)).toBeVisible();
    expect(await gateMarks(page, "dialog[open]")).toContain(
      "approve 버튼: 확인했어요, 복약 일정에 넣을게요",
    );
    expect(writes(calls, "POST", /\/medications$/)).toEqual([]);

    await sheet.getByRole("button", { name: /언제까지/ }).click();
    await page.getByRole("button", { name: /^오늘, / }).click();
    await page.getByRole("button", { name: "고르기" }).click();
    await expect(submit).toBeEnabled();
    // 🚨 게이트의 마지막 확인은 버튼 옆 caution 글자다. 색 전환(120ms)이 끝나기를 기다린다.
    await expect
      .poll(() => gateMarks(page, "dialog[open]"))
      .toEqual(
        expect.arrayContaining([expect.stringMatching(/^caution 색: 누르면 복약 일정이 등록되고/)]),
      );

    await submit.click();
    await expect(sheet.getByText("넣었어요", { exact: true })).toBeVisible();
    const submitted = writes(calls, "POST", /\/children\/c1\/medications$/);
    expect(submitted).toHaveLength(1);
    expect(submitted[0].idempotencyKey).toBeTruthy();
    // 🚨 서버가 계산하는 시각 · 화면 전용 키는 싣지 않는다.
    expect(JSON.stringify(submitted[0].body)).not.toMatch(/scheduled_time|draft_id|notice_times/);
    await expect(sheet.getByText("2건 중 1건 넣었어요.")).toBeVisible();
  });

  test("수정 초안은 '19:30 → 20:00' 을 보여 주고 PATCH 로 넣는다", async ({ page }) => {
    const calls = recordApi(page);
    await sendFromHome(page, "해열 시럽 저녁은 8시에");
    await turn(page, "해열 시럽 저녁은 8시에")
      .getByRole("button", { name: "확인하고 넣기" })
      .click();

    const sheet = page.getByRole("dialog", { name: "찾은 복약" });
    await sheet.getByRole("button", { name: "다음" }).click();
    await expect(sheet.getByText("2건 중 2번째")).toBeVisible();
    await expect(sheet.getByText("이렇게 달라져요")).toBeVisible();
    await expect(
      sheet.getByText("08:30, 12:30, 19:30", { exact: true }).filter({ visible: true }),
    ).toBeVisible();
    await expect(sheet.getByText("08:30, 12:30, 20:00", { exact: true }).first()).toBeVisible();

    await sheet.getByRole("button", { name: "확인했어요, 이대로 바꿀게요" }).click();
    await expect(sheet.getByText("넣었어요", { exact: true })).toBeVisible();
    const patched = writes(calls, "PATCH", /\/children\/c1\/medications\/ms_seed$/);
    expect(patched).toHaveLength(1);
    expect(patched[0].idempotencyKey).toBeTruthy();
  });

  test("🚨 넣고 다음 장으로 넘겼다 돌아와도 '넣었어요' 그대로다 — 다시 넣을 길이 없고 쓰기는 한 번이다", async ({
    page,
  }) => {
    const calls = recordApi(page);
    await sendFromHome(page, "기침 시럽 하루 세 번 식후");
    await turn(page, "기침 시럽 하루 세 번 식후")
      .getByRole("button", { name: "확인하고 넣기" })
      .click();
    const sheet = page.getByRole("dialog", { name: "찾은 복약" });

    await sheet.getByRole("button", { name: /언제까지/ }).click();
    await page.getByRole("button", { name: /^오늘, / }).click();
    await page.getByRole("button", { name: "고르기" }).click();
    await sheet.getByRole("button", { name: "확인했어요, 복약 일정에 넣을게요" }).click();
    await expect(sheet.getByText("2건 중 1건 넣었어요.")).toBeVisible();

    await sheet.getByRole("button", { name: "다음" }).click();
    await expect(sheet.getByText("2건 중 2번째")).toBeVisible();
    await sheet.getByRole("button", { name: "이전" }).click();
    await expect(sheet.getByText("2건 중 1번째")).toBeVisible();

    // 넘기면서 카드가 처음 상태로 되살아나면 새 키로 한 번 더 넣을 수 있었다 (#537 리뷰).
    await expect(sheet.getByText("넣었어요", { exact: true })).toBeVisible();
    await expect(
      sheet.getByRole("button", { name: "확인했어요, 복약 일정에 넣을게요" }),
    ).toHaveCount(0);
    expect(writes(calls, "POST", /\/medications$/)).toHaveLength(1);
  });

  test("시트를 닫으면 아무것도 부르지 않는다", async ({ page }) => {
    const calls = recordApi(page);
    await sendFromHome(page, "기침 시럽 하루 세 번 식후");
    await turn(page, "기침 시럽 하루 세 번 식후")
      .getByRole("button", { name: "확인하고 넣기" })
      .click();
    const sheet = page.getByRole("dialog", { name: "찾은 복약" });
    await sheet.getByRole("button", { name: "닫기" }).click();
    await expect(sheet).toBeHidden();
    expect(writes(calls, undefined, /medication/)).toEqual([]);
  });
});

test.describe("med_question — 답을 받는 Agent 질문", () => {
  test.use({ scenario: "med_question" });

  test("답하기가 서고, 답은 원문 없이 reply_to 로 가고, Health 가 이어받는다", async ({ page }) => {
    const calls = recordApi(page);
    await sendFromHome(page, "해열 시럽 그만 먹일래");
    const reply = turn(page, "해열 시럽 그만 먹일래");
    await expect(
      reply.getByText("해열 시럽 복약 알림을 멈출까요?", { exact: false }),
    ).toBeVisible();
    // 질문이 오면 입력창이 그 질문을 대상으로 잡는다 (Memory 질문과 같은 길).
    await expect(page.getByText("위 질문에 답하는 중")).toBeVisible();

    const input = page.getByRole("textbox", { name: "오늘 있었던 일" });
    await input.fill("네");
    await page.getByRole("button", { name: "이 이야기 남기기" }).click();
    await expect(
      turn(page, "네").getByText("해열 시럽 복약 알림을 멈췄어요. 먹인 기록은 그대로 남아요."),
    ).toBeVisible();
    await expect(reply.getByText("아래에 답해 주셨어요.")).toBeVisible();

    const sent = writes(calls, "POST", /\/inputs$/);
    expect(sent).toHaveLength(2);
    expect(sent[1].body).toMatchObject({ text: "네" });
    expect((sent[1].body as { reply_to?: string }).reply_to).toBeTruthy();
    expect(JSON.stringify(sent[1].body)).not.toContain("그만 먹일래");
  });
});

test.describe("med_handoff — 회차 현황 · 전달 서류", () => {
  test.use({ scenario: "med_handoff" });

  test("🚨 서류에 복사 버튼이 서고, 응급 카드의 조회 실패 줄은 그대로다 · 허용으로 읽힐 표시가 없다", async ({
    page,
  }) => {
    await sendFromHome(page, "어린이집에 낼 투약의뢰서 만들어줘");
    const reply = turn(page, "어린이집에 낼 투약의뢰서 만들어줘");

    await expect(reply.getByText("보호자 확인 후 서명해 주세요.", { exact: false })).toBeVisible();
    await expect(
      reply.getByText("알레르기 · 만성질환 : 불러오지 못했어요. 직접 확인해 주세요.", {
        exact: false,
      }),
    ).toBeVisible();
    await expect(reply.getByRole("button", { name: "복사하기" })).toHaveCount(2);
    // 🚨 회차 현황은 사실만 — 체크하는 곳으로 가는 길만 있다. 게이트 표식도 없다.
    await expect(reply.getByRole("link", { name: "복약 화면 열기" })).toBeVisible();
    expect(await gateMarks(page)).toEqual([]);
  });
});

test.describe("11-3 복약 — 회차 체크 (게이트가 아니다)", () => {
  test("체크하면 기록되고 풀 수 있다 · 게이트 표식이 없다 · 키를 달지 않는다", async ({ page }) => {
    const calls = recordApi(page);
    await page.goto(`/child/${CHILD}/profile`);
    await page.getByRole("link", { name: /해열 시럽/ }).click();
    await page.waitForURL(`**/child/${CHILD}/profile/medications`);

    await expect(page.getByRole("heading", { name: "복약" })).toBeVisible();
    await expect(page.getByText("오늘 체크 0/3회")).toBeVisible();
    expect(await gateMarks(page)).toEqual([]);

    const today = page
      .locator("section")
      .filter({ has: page.getByRole("heading", { name: /^오늘 · / }) });
    await today.getByText("08:30").first().click();
    await expect(page.getByText("오늘 체크 1/3회")).toBeVisible();
    await today.getByText("08:30").first().click();
    await expect(page.getByText("오늘 체크 0/3회")).toBeVisible();

    // 화면은 바로 바뀌고 요청은 줄을 서서 나간다 (`lib/serial-queue.ts`) — 두 번째 요청을 기다린다.
    const checksOf = () => writes(calls, "PUT", /\/medication-doses\/.+\/check$/);
    await expect
      .poll(() => checksOf().map((c) => c.body))
      .toEqual([{ checked: true }, { checked: false }]);
    const checks = checksOf();
    expect(checks.every((c) => c.idempotencyKey === undefined)).toBe(true);
  });

  test.describe("진행 중 복약이 없으면", () => {
    test.use({ scenario: "empty" });

    test("빈 상태가 건수 0 을 그대로 말한다", async ({ page }) => {
      await page.goto(`/child/${CHILD}/profile/medications`);
      await expect(page.getByText("지금 먹이는 약이 없어요")).toBeVisible();
    });
  });
});
