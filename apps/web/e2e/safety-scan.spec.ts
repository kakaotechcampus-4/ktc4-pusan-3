import type { Page } from "@playwright/test";

import { CHILD, expect, gateMarks, recordApi, test, writes } from "./fixtures";

/**
 * 11-2 검사지에서 가져오기 — 승인 게이트 ㉡ 가 시트에서 화면으로 옮겨 온 자리 (apps/web/CLAUDE.md §3).
 *
 * 🚨 알레르기 · 검진 · 건강 정보는 LLM 이 생성 · 추론 · 수정하지 않는다 (최상위 §2 안전).
 *    검사지에서 읽은 줄은 **보호자가 원문과 대조해 확인한 것만** 등록된다. 못 읽은 칸은 비어 있고
 *    사람이 채운다 — 화면이 기본값으로 채우지 않는다.
 *
 * 목 검사지(`mocks/fixtures.ts` 의 `safetyScan`): 확인이 필요해요 2건(이름 못 읽음,
 * 집먼지진드기 = 원문 못 읽음) · 잘 읽었어요 7건 · 이미 등록되어 있어요 1건(우유).
 */

/** 1×1 투명 PNG. 목은 사진 내용을 읽지 않는다. */
const PNG = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=",
  "base64",
);

async function openScan(page: Page) {
  await page.goto(`/child/${CHILD}/profile`);
  await page.getByRole("button", { name: "알레르기·건강 기록 추가" }).click();
  const chooser = page.waitForEvent("filechooser");
  await page.getByRole("button", { name: /^검사지 사진에서 가져오기/ }).click();
  await (await chooser).setFiles({ name: "scan.png", mimeType: "image/png", buffer: PNG });
  await page.waitForURL(`**/child/${CHILD}/profile/safety-scan`);
  await expect(page.getByRole("button", { name: "확인했어요, 7건 등록할게요" })).toBeVisible();
}

test("게이트 화면 — 네비 없이, 아무것도 저장되지 않았다고 말하고, 확인 안 한 줄은 등록하지 않는다", async ({
  page,
}) => {
  await openScan(page);
  // 흐름 중인 화면이다 — 고르다 마는 길을 만들지 않는다.
  await expect(page.getByRole("navigation", { name: "화면 이동" })).toHaveCount(0);
  await expect(page.getByText("검사지에 적힌 것만 옮겼어요")).toBeVisible();
  await expect(
    page.getByText("지금까지는 아무것도 저장되지 않았어요. 아래를 눌러야 등록돼요."),
  ).toBeVisible();
  await expect(page.getByText("확인하지 않은 2건은 등록하지 않아요.")).toBeVisible();

  const marks = await gateMarks(page);
  expect(marks).toContain("approve 버튼: 확인했어요, 7건 등록할게요");
  expect(marks.some((m) => m.startsWith("caution 색:"))).toBe(true);
});

test("이름을 못 읽은 줄은 사람이 채우기 전에는 고를 수 없다", async ({ page }) => {
  await openScan(page);
  const unread = page.getByRole("checkbox", { name: /^이름을 읽지 못했어요/ });
  await expect(page.getByText("이름을 읽지 못했어요. 검사지를 보고 채워주세요.")).toBeVisible();
  await unread.click({ force: true });
  await expect(unread).not.toBeChecked();
  await expect(page.getByRole("button", { name: "확인했어요, 7건 등록할게요" })).toBeVisible();
  await expect(page.getByText("확인하지 않은 2건은 등록하지 않아요.")).toBeVisible();

  // 대조군 — 잘 읽은 줄은 같은 방법으로 눌러서 뺄 수 있다 (위 단언이 헛클릭이 아니라는 확인).
  await page.getByRole("checkbox", { name: /^달걀흰자/ }).click({ force: true });
  await expect(page.getByRole("button", { name: "확인했어요, 6건 등록할게요" })).toBeVisible();
});

// 한동안 체크가 칸이 찼는지만 봐서, 원문과 대조하지 않은 줄이 체크 한 번으로 승인 목록에 들어갔다.
// 요약은 그때도 "확인하지 않은 2건은 등록하지 않아요" 라고 말했다 (#242).
test("원문을 못 읽은 줄도 고치기에서 확인하기 전에는 고를 수 없다", async ({ page }) => {
  await openScan(page);
  const mite = page.getByRole("checkbox", { name: /^집먼지진드기/ });
  await mite.click({ force: true });
  await expect(mite).not.toBeChecked({ timeout: 2_000 });
  await expect(page.getByRole("button", { name: "확인했어요, 7건 등록할게요" })).toBeVisible({
    timeout: 2_000,
  });
});

test("원문을 못 읽은 줄은 고치기에서 보고 확인하면 그때 승인 목록에 들어간다", async ({ page }) => {
  await openScan(page);
  const mite = page.getByRole("checkbox", { name: /^집먼지진드기/ });
  await mite
    .locator("xpath=ancestor::div[contains(@class,'rounded-card')][1]")
    .getByRole("button", { name: "고치기" })
    .click();
  const sheet = page.getByRole("dialog", { name: "옮겨 적은 것 고치기" });
  await expect(
    sheet.getByText("이 줄은 검사지 원문을 읽지 못했어요. 검사지를 직접 보고 적어주세요."),
  ).toBeVisible();
  await sheet.getByRole("button", { name: "이 내용으로 확인" }).click();

  await expect(sheet).toBeHidden();
  await expect(mite).toBeChecked();
  await expect(page.getByRole("button", { name: "확인했어요, 8건 등록할게요" })).toBeVisible();
  await expect(page.getByText("확인하지 않은 1건은 등록하지 않아요.")).toBeVisible();
});

test("승인하면 고른 줄마다 서로 다른 Idempotency-Key 로 등록하고, 다른 쓰기 경로는 없다", async ({
  page,
}) => {
  const calls = recordApi(page);
  await openScan(page);
  expect(writes(calls, "POST", /\/health-safety$/)).toEqual([]);

  await page.getByRole("button", { name: "확인했어요, 7건 등록할게요" }).click();
  await page.waitForURL(`**/child/${CHILD}/profile`);

  const saved = writes(calls, "POST", /\/health-safety$/);
  expect(saved).toHaveLength(7);
  const keys = new Set(saved.map((c) => c.idempotencyKey));
  expect(keys.has(undefined)).toBe(false);
  expect(keys.size).toBe(7);
  for (const call of saved) expect(call.body).toMatchObject({ type: "allergy" });

  // 검사지를 읽는 요청 말고는 이 흐름에서 쓰는 곳이 없다.
  const others = writes(calls).filter((c) => !/\/health-safety(\/scan)?$/.test(c.path));
  expect(others).toEqual([]);
});

test("새로고침하면 검사지 사진은 사라지고 다시 올라가지 않는다 (의료 기록 · persist 금지)", async ({
  page,
}) => {
  const calls = recordApi(page);
  await openScan(page);
  const scans = () => writes(calls, "POST", /\/health-safety\/scan$/).length;
  expect(scans()).toBe(1);
  expect(
    await page.evaluate(() =>
      [localStorage, sessionStorage].some((s) =>
        Object.keys(s).some((k) => /scan/i.test(k) || (s.getItem(k) ?? "").includes("blob:")),
      ),
    ),
  ).toBe(false);

  await page.reload();
  await expect(page.getByText("고른 검사지가 없어요")).toBeVisible();
  expect(scans()).toBe(1);
});
