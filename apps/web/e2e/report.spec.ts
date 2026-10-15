import type { Page } from "@playwright/test";

import { CHILD, expect, expectNoGate, recordApi, test, writes } from "./fixtures";

/**
 * 10 설정 › 문제 알리기 (#487 · 서버 PR #488).
 *
 * Google Play 정책(AI 생성 콘텐츠 · 사용자 제작 콘텐츠)이 **앱을 떠나지 않고** 알릴 길을 요구한다.
 * 그래서 설정 안에서 들어가고, 화면이 서버로 바로 보내는지(`mailto:` 가 아닌지)를 본다.
 * 승인 게이트도 되돌릴 수 없는 5곳도 아니다 — 표식도 `Idempotency-Key` 도 없어야 한다.
 *
 * 🚨 본문은 지어낸 문장이다 — 실제 보호자의 말을 넣지 않는다 (최상위 §9).
 */

const SETTINGS = `/child/${CHILD}/settings`;
const REPORT = `${SETTINGS}/report`;
const MESSAGE = "지어낸 문장이에요. 추천 문구가 이상했어요.";
const EMAIL = "parkhajh0803.dev@gmail.com";

/** 숨은 라디오 위를 라벨이 덮고 있다 — 사람이 누르는 자리(글자)를 누른다 (`ChoiceField` 와 같다). */
async function choose(page: Page, label: string) {
  await page.getByText(label, { exact: true }).click();
  await expect(page.getByRole("radio", { name: label })).toBeChecked();
}

function messageBox(page: Page) {
  return page.getByLabel("어떤 일이었는지 적어 주세요");
}

test("설정에서 들어가 고르고 적어 보내면 앞뒤 공백을 뗀 두 필드만 나가고 안내가 선다", async ({
  page,
}) => {
  const calls = recordApi(page);
  await page.goto(SETTINGS);
  await page.getByRole("link", { name: /문제 알리기/ }).click();
  await expect(page).toHaveURL(new RegExp(`${REPORT}$`));
  await expect(page.getByRole("heading", { name: "문제 알리기" })).toBeVisible();

  // 흐름 중인 화면이라 하단 네비가 없다 (탈퇴와 같다).
  await expect(page.getByRole("navigation", { name: "화면 이동" })).toHaveCount(0);
  // 답이 필요한 요청은 이메일로 — 이 양식이 공식 열람 · 삭제 창구로 읽히지 않게 적기 전에 말한다.
  await expect(page.getByRole("link", { name: EMAIL })).toHaveAttribute("href", `mailto:${EMAIL}`);

  const send = page.getByRole("button", { name: "보내기" });
  // 처음엔 아무것도 골라져 있지 않다 — 기본값으로 분류가 틀린 채 쌓이지 않게.
  await expect(page.getByRole("radio", { checked: true })).toHaveCount(0);
  await expect(send).toBeDisabled();

  await choose(page, "AI 답이나 추천이 부적절해요");
  await expect(send).toBeDisabled();

  await messageBox(page).fill("  \n ");
  await expect(send).toBeDisabled();

  const typed = `  ${MESSAGE}  \n`;
  await messageBox(page).fill(typed);
  await expect(page.getByText(new RegExp(`${typed.length}/1000$`))).toBeVisible();
  await expect(send).toBeEnabled();
  await expectNoGate(page);

  await send.click();
  await expect(page.getByText("알려 주셔서 고마워요. 확인하고 필요하면 조치할게요.")).toBeVisible();
  // 양식은 안내로 갈아 끼워진다 — 적은 글이 화면에 남지 않는다.
  await expect(messageBox(page)).toHaveCount(0);
  await expectNoGate(page);

  const posts = writes(calls, "POST", /^\/reports$/);
  expect(posts).toHaveLength(1);
  // 🚨 `toEqual` 이라 필드가 더 붙어도 깨진다 — 서버는 모르는 필드를 400 으로 거절한다.
  expect(posts[0]!.body).toEqual({ category: "ai_content", message: MESSAGE });
  expect(posts[0]!.idempotencyKey).toBeUndefined();
  // 🚨 적은 글을 브라우저 저장소에 남기지 않는다.
  const stored = await page.evaluate(() =>
    [localStorage, sessionStorage]
      .flatMap((store) => Object.keys(store).map((key) => store.getItem(key) ?? ""))
      .join("\n"),
  );
  expect(stored).not.toContain("추천 문구가 이상했어요");

  await page.getByRole("link", { name: "마치고 돌아가기" }).click();
  await expect(page).toHaveURL(new RegExp(`${SETTINGS}$`));
});

test("빠르게 두 번 눌러도 한 번만 보낸다", async ({ page }) => {
  const calls = recordApi(page);
  await page.goto(REPORT);
  await choose(page, "공동 기록이나 다른 보호자에게 문제가 있어요");
  await messageBox(page).fill(MESSAGE);

  await page.getByRole("button", { name: "보내기" }).dblclick();
  await expect(page.getByText("알려 주셔서 고마워요. 확인하고 필요하면 조치할게요.")).toBeVisible();

  const posts = writes(calls, "POST", /^\/reports$/);
  expect(posts).toHaveLength(1);
  expect(posts[0]!.body).toEqual({ category: "shared_record", message: MESSAGE });
});

test.describe("오늘 보낼 수 있는 만큼 보냈으면", () => {
  test.use({ scenario: "report_limit" });

  test("서버 문구를 그대로 세우고, 다시 시도 없이 보내기를 잠그고, 적은 글은 남긴다", async ({
    page,
  }) => {
    const calls = recordApi(page);
    await page.goto(REPORT);
    await choose(page, "앱이 이상하게 동작해요");
    await messageBox(page).fill(MESSAGE);
    await page.getByRole("button", { name: "보내기" }).click();

    await expect(
      page.getByText("오늘은 더 보낼 수 없어요. 급한 일이면 이메일로 알려 주세요."),
    ).toBeVisible();
    // 누를 때마다 같은 429 다 — 다시 시도를 세우지 않고 보내기도 잠근다.
    await expect(page.getByRole("button", { name: "보내기" })).toBeDisabled();
    await expect(page.getByRole("button", { name: /다시/ })).toHaveCount(0);
    // 이메일로 옮겨 적을 수 있게 글은 그대로다.
    await expect(messageBox(page)).toHaveValue(MESSAGE);
    await expect(page.getByRole("link", { name: EMAIL })).toBeVisible();
    await expect(page.getByText("알려 주셔서 고마워요", { exact: false })).toHaveCount(0);

    expect(writes(calls, "POST", /^\/reports$/)).toHaveLength(1);
    await expectNoGate(page);
  });
});
