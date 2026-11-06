import type { Page } from "@playwright/test";

import { MEDICATION_ENABLED } from "@/lib/store-policy";

import { CHILD, expect, recordApi, test, type ApiCall } from "./fixtures";

/**
 * 복약을 꺼 둔 동안 (`lib/store-policy.ts` · 11-07) — 화면 어디에도 복약으로 들어가는 길이 서지 않는다.
 *
 * 켜져 있으면 이 파일은 건너뛰고 `medication.spec.ts` 가 돈다.
 * 🚨 지어낸 약 이름이다 (최상위 §9).
 */
test.skip(MEDICATION_ENABLED, "복약이 켜져 있다 — medication.spec.ts 가 건다");

async function sendFromHome(page: Page, text: string) {
  await page.goto(`/child/${CHILD}/home`);
  await page.getByRole("textbox", { name: "오늘 있었던 일" }).fill(text);
  await page.getByRole("button", { name: "이 이야기 남기기" }).click();
  await page.waitForURL(`**/child/${CHILD}/chat`);
}

function turn(page: Page, text: string) {
  return page.getByRole("listitem").filter({ hasText: text }).first();
}

function medicationReads(calls: ApiCall[]) {
  return calls.filter((c) => /\/medications$/.test(c.path));
}

test("11 프로필에 복약 칸이 없고, 머리말도 복약을 말하지 않는다", async ({ page }) => {
  const calls = recordApi(page);
  await page.goto(`/child/${CHILD}/profile`);

  await expect(page.getByText("기본 정보와 키·몸무게, 알레르기를 여기서 관리해요.")).toBeVisible();
  await expect(page.getByRole("heading", { name: "알레르기 · 건강" })).toBeVisible();
  await expect(page.getByText("복약")).toHaveCount(0);
  expect(medicationReads(calls)).toEqual([]);
});

test("03 홈은 그대로 뜨고, 오늘 먹일 약이 없고, 복약을 부르지 않는다", async ({ page }) => {
  const calls = recordApi(page);
  await page.goto(`/child/${CHILD}/home`);

  // 🚨 꺼 둔 조회를 기다리느라 스켈레톤에 멈추지 않는다.
  await expect(page.getByText("이번 주 기록")).toBeVisible();
  await expect(page.getByText("오늘 먹일 약")).toHaveCount(0);
  expect(medicationReads(calls)).toEqual([]);
});

test("11-3 복약 주소로 바로 들어오면 프로필로 돌려보낸다", async ({ page }) => {
  const calls = recordApi(page);
  await page.goto(`/child/${CHILD}/profile/medications`);

  await page.waitForURL(`**/child/${CHILD}/profile`);
  await expect(page.getByRole("heading", { name: "아이 프로필" })).toBeVisible();
  // 돌려보내기 전에 화면을 세워 복약을 부르지 않는다.
  expect(medicationReads(calls)).toEqual([]);
});

test.describe("04 대화 — 복약 초안", () => {
  test.use({ scenario: "med_draft" });

  test("복약 초안 카드가 서지 않는다", async ({ page }) => {
    await sendFromHome(page, "기침 시럽 하루 세 번 식후");
    const reply = turn(page, "기침 시럽 하루 세 번 식후");

    // 결과 카드는 run 이 끝난 뒤에만 선다 — 초안 프레임은 그보다 먼저 왔다.
    // 🚨 보낸 말에 없고 복약과도 상관없는 글자(Agent 이름표)로 기다린다. "기침 시럽" 은 보낸 말풍선에도 있어
    //    결과 전에 통과하고, 복약 문구는 서버에서 끄면 사라진다.
    await expect(reply.getByText("건강", { exact: true }).first()).toBeVisible();
    await expect(reply.getByText("복약 일정으로 넣을까요?")).toHaveCount(0);
    await expect(reply.getByRole("button", { name: "확인하고 넣기" })).toHaveCount(0);
  });
});

test.describe("04 대화 — 복용 체크 안내", () => {
  test.use({ scenario: "med_check" });

  test("'복약 화면 열기' 가 서지 않는다", async ({ page }) => {
    await sendFromHome(page, "약 먹였어");
    const reply = turn(page, "약 먹였어");

    await expect(reply.getByText("건강", { exact: true }).first()).toBeVisible();
    await expect(reply.getByRole("link", { name: "복약 화면 열기" })).toHaveCount(0);
  });
});

test.describe("00 소개", () => {
  test.use({ signedIn: false });

  test("건강 설명에 복약 일정이 없다", async ({ page }) => {
    await page.goto("/");

    await expect(page.getByText("증상 기록을 모아 정리해요.", { exact: false })).toBeVisible();
    await expect(page.getByText("복약", { exact: false })).toHaveCount(0);
  });
});
