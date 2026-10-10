import type { Page } from "@playwright/test";

import { CHILD, expect, recordApi, test, writes } from "./fixtures";

/**
 * 03 홈 → 04 대화 — 한 줄 입력의 결과가 §2 를 지키며 그려지는가.
 *
 * 어느 run 에 답하는가 · 질문을 언제 닫는가 · 다시 시도가 같은 키인가는 `stores/conversation.test.ts`
 * 가 스토어 수준에서 건다. 여기서는 그 상태가 **어떤 글자와 버튼으로 서는가**를 본다.
 *
 * 🚨 목은 발화의 **낱말만** 보고 기록형 · 일정형 · 혼합형을 가른다 (`mocks/handlers/runs.ts`).
 *    `가기` 가 있으면 일정형, `먹었` · `했대` 가 있으면 기록형, 아무것도 안 걸리면 혼합형이다.
 */

async function sendFromHome(page: Page, text: string) {
  await page.goto(`/child/${CHILD}/home`);
  await page.getByRole("textbox", { name: "오늘 있었던 일" }).fill(text);
  await page.getByRole("button", { name: "이 이야기 남기기" }).click();
  await page.waitForURL(`**/child/${CHILD}/chat`);
}

/** 그 한 줄의 답 묶음 (말풍선 하나 = `li` 하나). */
function turn(page: Page, text: string) {
  return page.getByRole("listitem").filter({ hasText: text }).first();
}

test.describe("일정은 승인 전에는 저장하지 않는다 (§2 실행)", () => {
  test('일정형 한 줄은 "저장했어요" 라고 말하지 않고, 카드를 누르기 전에는 캘린더에 쓰지 않는다', async ({
    page,
  }) => {
    const calls = recordApi(page);
    await sendFromHome(page, "이번 주말에 공원 산책 가기 저장해줘");
    const reply = turn(page, "이번 주말에 공원 산책 가기 저장해줘");

    await expect(reply.getByRole("heading", { name: "이렇게 이해했어요" })).toBeVisible();
    await expect(
      reply.getByText("일정만 찾았어요. 아이에 관한 기록으로 쌓을 것은 없어서 저장하지 않았어요."),
    ).toBeVisible();
    await expect(reply.getByText("이렇게 저장했어요")).toHaveCount(0);
    await expect(reply.getByText(/기록 \d+건 저장됨/)).toHaveCount(0);
    await expect(reply.getByText("적어주신 말에서 일정 1건을 찾았어요.")).toBeVisible();

    // 🚨 자동 실행 경로가 없다 — Agent 는 초안까지만 만든다.
    expect(writes(calls, "POST", /\/events$/)).toEqual([]);
    expect(writes(calls, "PATCH", /\/events\//)).toEqual([]);
  });

  test("초안 시트의 제출이 곧 승인 게이트 ㉠ 이고 Idempotency-Key 를 단다", async ({ page }) => {
    const calls = recordApi(page);
    await sendFromHome(page, "지어낸 한 줄");
    const reply = turn(page, "지어낸 한 줄");
    await reply.getByRole("button", { name: "확인하고 넣기" }).click();

    const sheet = page.getByRole("dialog", { name: "찾은 일정" });
    await expect(sheet.getByText("2건 중 1번째")).toBeVisible();
    await expect(sheet.getByText("아직 저장되지 않았어요")).toBeVisible();
    expect(writes(calls, "POST", /\/events$/)).toEqual([]);

    await sheet.getByRole("button", { name: "확인했어요, 캘린더에 넣을게요" }).click();
    await expect(sheet.getByText("2건 중 1건 넣었어요.")).toBeVisible();
    const submitted = writes(calls, "POST", /\/children\/c1\/events$/);
    expect(submitted).toHaveLength(1);
    expect(submitted[0].idempotencyKey).toBeTruthy();
  });
});

test.describe("성공과 실패 · 안내를 한 화면에 (§2 실행 NF-06)", () => {
  test.describe("partial", () => {
    test.use({ scenario: "partial" });

    test("Agent 하나가 실패해도 저장된 기록이 그대로 선다", async ({ page }) => {
      await sendFromHome(page, "지어낸 한 줄");
      const reply = turn(page, "지어낸 한 줄");
      await expect(reply.getByRole("heading", { name: "이렇게 저장했어요" })).toBeVisible();
      await expect(reply.getByText("기록 2건 저장됨")).toBeVisible();
      await expect(
        reply.getByText("놀이 쪽은 이번에 처리하지 못했어요. 저장한 기록은 그대로 남아 있어요."),
      ).toBeVisible();
      await expect(reply.getByText("읽지 못했어요")).toHaveCount(0);
    });

    test('저장한 것이 없으면 "저장한 기록은 그대로" 라고 말하지 않는다 (PR #215)', async ({
      page,
    }) => {
      await sendFromHome(page, "주말에 소풍 가기");
      const reply = turn(page, "주말에 소풍 가기");
      await expect(
        reply.getByText("놀이 쪽은 이번에 처리하지 못했어요.", { exact: true }),
      ).toBeVisible();
      await expect(reply.getByText("저장한 기록은 그대로", { exact: false })).toHaveCount(0);
    });
  });

  test.describe("guidance — 알레르기는 대신 저장하지 않는다 (§2 안전)", () => {
    test.use({ scenario: "guidance" });

    test("저장 0건이라고 말하고, 직접 입력하는 곳은 11 프로필이다", async ({ page }) => {
      await sendFromHome(page, "땅콩 알레르기 있어");
      const reply = turn(page, "땅콩 알레르기 있어");
      await expect(reply.getByRole("heading", { name: "적어주신 말을 확인했어요" })).toBeVisible();
      await expect(reply.getByText("안전 정보", { exact: true })).toBeVisible();
      await expect(reply.getByRole("link", { name: "직접 입력하러 가기" })).toHaveAttribute(
        "href",
        `/child/${CHILD}/profile`,
      );
      await expect(reply.getByText("이렇게 저장했어요")).toHaveCount(0);
      await expect(reply.getByText(/기록 \d+건 저장됨/)).toHaveCount(0);
      // 안내가 이미 이유를 말했다 — 그 아래에서 다른 이유를 대지 않는다.
      await expect(
        reply.getByText("아이에 관한 기록은 찾지 못했어요", { exact: false }),
      ).toHaveCount(0);
    });
  });

  test.describe("guidance_mixed — 한 줄에 저장과 안내가 섞였다", () => {
    test.use({ scenario: "guidance_mixed" });

    test("안내 카드가 저장된 것을 함께 말하고, 저장한 기록이 같은 화면에 선다", async ({
      page,
    }) => {
      await sendFromHome(page, "계란 잘 먹었어. 그리고 땅콩 알레르기 있어");
      const reply = turn(page, "계란 잘 먹었어. 그리고 땅콩 알레르기 있어");
      await expect(reply.getByRole("heading", { name: "이렇게 저장했어요" })).toBeVisible();
      await expect(reply.getByText("아래 기록은 그대로 저장했어요.")).toBeVisible();
      await expect(reply.getByText("기록 2건 저장됨")).toBeVisible();
    });
  });

  test.describe("unavailable — 아직 없는 Agent", () => {
    test.use({ scenario: "unavailable" });

    test("준비 중 안내와 저장된 기록이 한 화면에 서고, 실패 카드로 그리지 않는다", async ({
      page,
    }) => {
      await sendFromHome(page, "지어낸 한 줄");
      const reply = turn(page, "지어낸 한 줄");
      const notice = reply.getByText("아직 준비 중", { exact: true });
      await expect(notice).toBeVisible();
      await expect(reply.getByText("아래 기록은 그대로 저장했어요.")).toBeVisible();
      await expect(reply.getByText("기록 2건 저장됨")).toBeVisible();
      await expect(reply.getByRole("status").filter({ hasText: "아직 준비 중" })).toHaveCount(0);
    });
  });
});

test.describe("되묻기의 답은 원문을 다시 보내지 않는다 (#158)", () => {
  test.use({ scenario: "note_mixed" });

  test("일부 저장 + 질문 — 답에는 원문이 없고 reply_to 가 그 run 을 가리킨다", async ({ page }) => {
    const calls = recordApi(page);
    const first = page.waitForResponse(
      (r) => r.request().method() === "POST" && r.url().endsWith(`/children/${CHILD}/inputs`),
    );
    await sendFromHome(page, "계란 잘 먹었어. 요즘 기침해");
    const { run_id: runId } = (await (await first).json()) as { run_id: string };

    const reply = turn(page, "계란 잘 먹었어. 요즘 기침해");
    await expect(reply.getByText("기록 1건 저장됨")).toBeVisible();
    await expect(page.getByText("위 질문에 답하는 중")).toBeVisible();
    // 🚨 원문을 입력창에 되돌리지 않는다 — 다시 보내면 이미 저장된 조각이 또 저장된다.
    const input = page.getByRole("textbox", { name: "오늘 있었던 일" });
    await expect(input).toHaveValue("");

    await input.fill("어제부터");
    await page.getByRole("button", { name: "이 이야기 남기기" }).click();
    await expect(turn(page, "어제부터").getByText("기록 1건 저장됨")).toBeVisible();

    const sent = writes(calls, "POST", /\/inputs$/);
    expect(sent).toHaveLength(2);
    expect(sent[1].body).toMatchObject({ text: "어제부터", reply_to: runId });
    expect(JSON.stringify(sent[1].body)).not.toContain("계란");
  });
});

test.describe("재질문 상한에 닿으면 질문 대신 안내로 끝난다 (#246)", () => {
  test.use({ scenario: "reply_ask_limit" });

  test("네 번째 질문의 답 뒤에는 답할 자리가 없고, 입력창은 새 이야기를 받는다", async ({
    page,
  }) => {
    await sendFromHome(page, "어제부터 기침해");
    const input = page.getByRole("textbox", { name: "오늘 있었던 일" });

    // 처음 질문까지 넷이다. 이어받은 run 이 셋을 더 묻는다.
    for (const answer of ["사흘 전부터", "하루 세 번쯤", "밤에도"]) {
      await expect(page.getByText("위 질문에 답하는 중")).toBeVisible();
      await input.fill(answer);
      await page.getByRole("button", { name: "이 이야기 남기기" }).click();
      await expect(
        turn(page, answer).getByRole("button", { name: "이 질문에 답하기" }),
      ).toHaveCount(0);
      await expect(turn(page, answer).getByText("아래 입력창에서 답하는 중이에요.")).toBeVisible();
    }

    await input.fill("집에서");
    await page.getByRole("button", { name: "이 이야기 남기기" }).click();
    const last = turn(page, "집에서");
    await expect(last.getByText("이번 내용은 저장하지 않았어요")).toBeVisible();

    // 🚨 안내 문장에 답할 자리를 열지 않는다 — 열면 상한이 의미가 없어진다.
    await expect(last.getByRole("button", { name: "이 질문에 답하기" })).toHaveCount(0);
    await expect(last.getByRole("heading", { name: "이렇게 저장했어요" })).toHaveCount(0);
    // 🚨 제목과 안내가 서로 다른 말을 하지 않는다 (#251 리뷰 — 안내에 "확인" 을 쓰지 않는다).
    await expect(last.getByText("확인하지 못해")).toHaveCount(0);
    await expect(turn(page, "밤에도").getByText("아래에 답해 주셨어요.")).toBeVisible();
    await expect(page.getByText("위 질문에 답하는 중")).toHaveCount(0);
    await expect(page.getByRole("button", { name: "이 질문에 답하기" })).toHaveCount(0);
  });
});

test.describe("보낸 원문은 디스크에 남지 않는다 (§2 개인정보)", () => {
  test("입력 중에도 보낸 뒤에도 localStorage · sessionStorage 에 원문이 없다", async ({ page }) => {
    const line = "지어낸 한 줄 Q7X";
    await page.goto(`/child/${CHILD}/home`);
    await page.getByRole("textbox", { name: "오늘 있었던 일" }).fill(line);
    expect(await storedText(page)).not.toContain("Q7X");

    await page.getByRole("button", { name: "이 이야기 남기기" }).click();
    await page.waitForURL(`**/child/${CHILD}/chat`);
    await expect(
      turn(page, line).getByRole("heading", { name: "이렇게 저장했어요" }),
    ).toBeVisible();
    expect(await storedText(page)).not.toContain("Q7X");

    // 메모리 전용이다 — 새로고침하면 그날 대화가 사라진다 (#228 전까지).
    await page.reload();
    await expect(page.getByText(line)).toHaveCount(0);
  });
});

function storedText(page: Page): Promise<string> {
  return page.evaluate(() =>
    [localStorage, sessionStorage]
      .flatMap((store) => Object.keys(store).map((k) => `${k}=${store.getItem(k)}`))
      .join("\n"),
  );
}
