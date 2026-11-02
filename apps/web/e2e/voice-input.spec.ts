import type { Page } from "@playwright/test";

import { CHILD, expect, recordApi, test, writes } from "./fixtures";

/**
 * 03 홈 · 04 대화 채팅바의 말로 적기 (#595 · #602 리뷰 · `lib/native/speech.ts`).
 *
 * 셸은 브라우저에 없으니 **셸이 꽂는 창구를 흉내 내서 심는다** — 목(MSW)이 아니다. 창구는 부탁을 받아 적기만
 * 하고, 결과는 테스트가 셸 대신 `icatch:speech` 이벤트로 쏜다. 부탁은 `sessionStorage` 에 적는다 — 문서가 통째로
 * 바뀌어도 남아야 "내려가는 문서가 멈춤을 보냈는가" 를 볼 수 있다.
 *
 * 🚨 음성이 휴대폰 밖으로 나가지 않는지(기기 안 인식)는 여기서 못 본다. 셸 쪽 판정은
 *    `apps/mobile/src/native/speech.test.ts`, 실제 동작은 Android 13+ 기기의 비행기 모드에서 본다.
 */

const CALLS_KEY = "e2e.speechCalls";

async function installShell(page: Page, available: boolean) {
  await page.addInitScript(
    ([ok, key]) => {
      const log = (call: string) => {
        const calls = JSON.parse(sessionStorage.getItem(key) ?? "[]") as string[];
        sessionStorage.setItem(key, JSON.stringify([...calls, call]));
      };
      window.icatch = {
        ...window.icatch,
        speech: { available: ok, start: () => log("start"), stop: () => log("stop") },
      };
    },
    [available, CALLS_KEY] as const,
  );
}

/** 셸 대신 이벤트를 쏜다. */
async function emit(page: Page, detail: unknown) {
  await page.evaluate((d) => {
    window.dispatchEvent(new CustomEvent("icatch:speech", { detail: d }));
  }, detail);
}

async function speechCalls(page: Page): Promise<string[]> {
  return page.evaluate(
    (key) => JSON.parse(sessionStorage.getItem(key) ?? "[]") as string[],
    CALLS_KEY,
  );
}

const input = (page: Page) => page.getByRole("textbox", { name: "오늘 있었던 일" });
const mic = (page: Page) => page.getByRole("button", { name: "말로 적기", exact: true });
const finish = (page: Page) => page.getByRole("button", { name: "다 말했어요" });
const discard = (page: Page) => page.getByRole("button", { name: "들은 말 지우기" });
const send = (page: Page) => page.getByRole("button", { name: "이 이야기 남기기" });

/** 채팅바 위의 한 줄. 🚨 `getByRole("status")` 만으로 찾지 않는다 — 토스트 자리도 status 다. */
const statusLine = (page: Page, text: string) => page.getByRole("status").filter({ hasText: text });
const LISTENING = "듣고 있어요. 말하면 입력창에 바로 적혀요.";

test.describe("말로 적기는 앱의 기기 안 인식으로만 켠다", () => {
  test("브라우저에서는 마이크가 비활성이고 이유를 말한다", async ({ page }) => {
    await page.goto(`/child/${CHILD}/home`);
    await expect(
      page.getByRole("button", { name: "말로 적기 (앱에서 쓸 수 있어요)" }),
    ).toBeDisabled();
  });

  test("기기 안 인식을 못 하는 휴대폰에서는 비활성이고 이유를 말한다", async ({ page }) => {
    await installShell(page, false);
    await page.goto(`/child/${CHILD}/home`);
    await expect(
      page.getByRole("button", { name: "말로 적기 (이 휴대폰에서는 쓸 수 없어요)" }),
    ).toBeDisabled();
  });
});

test.describe("들은 말은 입력창에 넣기만 하고 보내지 않는다", () => {
  test.beforeEach(async ({ page }) => {
    await installShell(page, true);
  });

  test("쓰던 글 뒤에 구간마다 이어 붙이고, 다 말했어요로 끝낸 뒤 보내기는 보호자가 누른다", async ({
    page,
  }) => {
    const calls = recordApi(page);
    await page.goto(`/child/${CHILD}/home`);
    await input(page).fill("아침에");

    await mic(page).click();
    expect(await speechCalls(page)).toEqual(["start"]);
    // 듣는 동안 알약은 [입력] [음량 막대] [들은 말 지우기] [다 말했어요] — 사진, 마이크, 보내기는 비킨다.
    await expect(finish(page)).toBeFocused();
    await expect(discard(page)).toBeVisible();
    await expect(mic(page)).toHaveCount(0);
    await expect(send(page)).toHaveCount(0);
    await expect(statusLine(page, LISTENING)).toBeVisible();
    // 붙일 자리를 누를 때의 글로 잡아 뒀다 — 듣는 동안 고친 글은 덮이므로 고칠 수 없게 한다.
    await expect(input(page)).toHaveAttribute("readonly", "");

    await emit(page, { type: "start" });
    await emit(page, { type: "result", text: "밥을", final: false });
    await expect(input(page)).toHaveValue("아침에 밥을");
    // 말하는 중인 구간은 같은 자리를 바꿔 끼운다.
    await emit(page, { type: "result", text: "밥을 잘 먹었어요", final: true });
    await expect(input(page)).toHaveValue("아침에 밥을 잘 먹었어요");
    // 다음 구간은 끝난 구간 뒤에 붙는다.
    await emit(page, { type: "result", text: "낮잠도 잤어요", final: true });
    await expect(input(page)).toHaveValue("아침에 밥을 잘 먹었어요 낮잠도 잤어요");

    // 🚨 듣는 동안 Enter 로도 보내지 않는다 — 보내기 버튼이 비켜 있는 것과 같은 말을 한다.
    await input(page).press("Enter");
    expect(writes(calls, "POST", /\/inputs$/)).toEqual([]);

    await finish(page).click();
    expect(await speechCalls(page)).toEqual(["start", "stop"]);
    await emit(page, { type: "end" });
    await expect(mic(page)).toBeFocused();
    await expect(input(page)).toHaveValue("아침에 밥을 잘 먹었어요 낮잠도 잤어요");
    await expect(input(page)).not.toHaveAttribute("readonly", "");
    await expect(statusLine(page, LISTENING)).toHaveCount(0);

    // 🚨 들은 말이 저절로 기록이 되지 않는다.
    expect(writes(calls, "POST", /\/inputs$/)).toEqual([]);
  });

  test("들은 말 지우기는 누를 때의 글로 되돌리고, 지난 듣기가 끝나기 전에는 새로 듣지 않는다", async ({
    page,
  }) => {
    await page.goto(`/child/${CHILD}/home`);
    await input(page).fill("아침에");
    await mic(page).click();
    await emit(page, { type: "result", text: "공원에서 놀았어요", final: true });
    await expect(input(page)).toHaveValue("아침에 공원에서 놀았어요");

    await discard(page).click();
    await expect(input(page)).toHaveValue("아침에");
    expect(await speechCalls(page)).toEqual(["start", "stop"]);
    await expect(mic(page)).toBeFocused();

    // 지난 듣기의 `end` 가 오기 전에 누르면 아무 일도 없다 — 늦은 글자가 새 글 뒤에 붙지 않게.
    await mic(page).click();
    expect(await speechCalls(page)).toEqual(["start", "stop"]);
    // 그만 듣기 뒤에도 마지막 구간이 한 번 더 올 수 있다 (`SpeechBridge.stop`). 와도 되돌린 글을 덮지 않는다.
    await emit(page, { type: "result", text: "그네도 탔어요", final: true });
    await expect(input(page)).toHaveValue("아침에");
    // 기다리는 동안 오는 `start` 는 버린 듣기다(셸이 권한을 확인하는 사이에 보냈을 때) — 멈춘다.
    await emit(page, { type: "start" });
    expect(await speechCalls(page)).toEqual(["start", "stop", "stop"]);

    await emit(page, { type: "end" });
    await mic(page).click();
    expect(await speechCalls(page)).toEqual(["start", "stop", "stop", "start"]);
  });

  test("권한 창이 뜨기 직전에 왜 마이크를 쓰는지 먼저 말한다", async ({ page }) => {
    // Android 권한 창에는 우리 문장이 나오지 않는다 (정보통신망법 제22조의2).
    await page.goto(`/child/${CHILD}/home`);
    await mic(page).click();
    await emit(page, { type: "asking" });
    await expect(
      statusLine(
        page,
        "말한 내용을 휴대폰 안에서 글자로 바꾸려고 마이크를 써요. 음성은 휴대폰 밖으로 보내지 않아요.",
      ),
    ).toBeVisible();

    await emit(page, { type: "start" });
    await expect(statusLine(page, LISTENING)).toBeVisible();
  });

  test("한 번 거절하면 다시 누르라고, 다시 물을 수 없으면 설정에서 켜라고 말한다", async ({
    page,
  }) => {
    await page.goto(`/child/${CHILD}/home`);
    await mic(page).click();
    await emit(page, { type: "error", reason: "declined" });
    await expect(
      statusLine(page, "마이크를 허용해야 말로 적기를 쓸 수 있어요. 다시 누르면 한 번 더 물어요."),
    ).toBeVisible();
    await expect(mic(page)).toBeFocused();

    await mic(page).click();
    await emit(page, { type: "error", reason: "denied" });
    await expect(
      statusLine(page, "마이크 권한이 꺼져 있어요. 휴대폰 설정에서 아이캐치의 마이크를 켜 주세요."),
    ).toBeVisible();
    await expect(statusLine(page, LISTENING)).toHaveCount(0);
  });

  test("채팅바가 시작하지 않은 듣기가 시작되면 멈춘다", async ({ page }) => {
    // 셸은 듣는데 화면은 꺼져 있는 상태를 남기지 않는다 (`useVoiceInput` 머리말).
    await page.goto(`/child/${CHILD}/home`);
    await expect(mic(page)).toBeEnabled();
    await emit(page, { type: "start" });
    expect(await speechCalls(page)).toEqual(["stop"]);
    await expect(finish(page)).toHaveCount(0);
  });

  test("듣는 중에 문서가 새로 열리면 셸에 멈춤을 보낸다", async ({ page }) => {
    // 새로고침 · 하드 이동에서는 React 정리가 돌지 않는다 — `pagehide` 가 보낸다 (#602 리뷰).
    await page.goto(`/child/${CHILD}/home`);
    await mic(page).click();
    await emit(page, { type: "start" });
    await page.evaluate(() => window.location.assign(window.location.pathname));
    await expect(mic(page)).toBeEnabled();
    expect(await speechCalls(page)).toEqual(["start", "stop"]);
  });
});

test.describe("듣는 중에 앱이 입력창에 넣은 원문을 들은 말이 덮지 않는다", () => {
  test.use({ scenario: "send_failed", allowFailedApi: true });

  test.beforeEach(async ({ page }) => {
    await installShell(page, true);
  });

  test("고쳐 쓰기로 돌아온 원문이 남고 듣기는 멈춘다", async ({ page }) => {
    // "고쳐 쓰기" 는 말풍선을 지우고 원문을 입력창에 넣는다. 듣는 중의 입력창은 비어 있어서 그 길이 열린다
    // (`canHandBack`). 그 뒤 결과가 누를 때의 글("") 뒤에 붙어 입력창을 바꾸면 원문이 어디에도 남지 않는다.
    await page.goto(`/child/${CHILD}/chat`);
    const original = "낮잠을 두 시간 잤어요";
    await input(page).fill(original);
    await send(page).click();
    const rewrite = page.getByRole("button", { name: "고쳐 쓰기" });
    await expect(rewrite).toBeVisible();

    await mic(page).click();
    await emit(page, { type: "start" });
    await rewrite.click();
    await expect(input(page)).toHaveValue(original);
    expect(await speechCalls(page)).toEqual(["start", "stop"]);

    await emit(page, { type: "result", text: "공원에 갔어요", final: false });
    await expect(input(page)).toHaveValue(original);
    await expect(mic(page)).toBeVisible();
  });
});
