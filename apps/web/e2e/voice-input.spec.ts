import type { Page } from "@playwright/test";

import { CHILD, expect, recordApi, test, writes } from "./fixtures";

/**
 * 03 홈 채팅바의 말로 적기 (#595 · `lib/native/speech.ts`).
 *
 * 셸은 브라우저에 없으니 **셸이 꽂는 창구를 흉내 내서 심는다** — 목(MSW)이 아니다. 창구는 부탁을 받아 적기만
 * 하고, 결과는 테스트가 셸 대신 `icatch:speech` 이벤트로 쏜다.
 *
 * 🚨 음성이 휴대폰 밖으로 나가지 않는지(기기 안 인식)는 여기서 못 본다. 셸 쪽 판정은
 *    `apps/mobile/src/native/speech.test.ts`, 실제 동작은 Android 13+ 기기의 비행기 모드에서 본다.
 */

async function installShell(page: Page, available: boolean) {
  await page.addInitScript((ok) => {
    const calls: string[] = [];
    Object.assign(window, { __speechCalls: calls });
    window.icatch = {
      ...window.icatch,
      speech: {
        available: ok,
        start: () => calls.push("start"),
        stop: () => calls.push("stop"),
      },
    };
  }, available);
}

/** 셸 대신 이벤트를 쏜다. */
async function emit(page: Page, detail: unknown) {
  await page.evaluate((d) => {
    window.dispatchEvent(new CustomEvent("icatch:speech", { detail: d }));
  }, detail);
}

async function speechCalls(page: Page): Promise<string[]> {
  return page.evaluate(() => (window as unknown as { __speechCalls: string[] }).__speechCalls);
}

const input = (page: Page) => page.getByRole("textbox", { name: "오늘 있었던 일" });

/** 마이크. 🚨 이름은 듣는 동안에도 같고 켜짐은 `aria-pressed` 가 말한다. */
const mic = (page: Page) => page.getByRole("button", { name: "말로 적기", exact: true });

/** 채팅바 위의 듣는 중 한 줄. 🚨 `getByRole("status")` 만으로 찾지 않는다 — 토스트 자리도 status 다. */
const listeningLine = (page: Page) =>
  page
    .getByRole("status")
    .filter({ hasText: "듣고 있어요. 다 말했으면 마이크를 다시 눌러 주세요." });

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

  test("쓰던 글 뒤에 구간마다 이어 붙이고, 보내기는 보호자가 누른다", async ({ page }) => {
    const calls = recordApi(page);
    await page.goto(`/child/${CHILD}/home`);
    await input(page).fill("아침에");

    await mic(page).click();
    expect(await speechCalls(page)).toEqual(["start"]);
    await expect(mic(page)).toHaveAttribute("aria-pressed", "true");
    await expect(listeningLine(page)).toBeVisible();
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

    await mic(page).click();
    expect(await speechCalls(page)).toEqual(["start", "stop"]);
    await emit(page, { type: "end" });
    await expect(mic(page)).toHaveAttribute("aria-pressed", "false");
    await expect(input(page)).not.toHaveAttribute("readonly", "");
    await expect(listeningLine(page)).toHaveCount(0);

    // 🚨 들은 말이 저절로 기록이 되지 않는다.
    expect(writes(calls, "POST", /\/inputs$/)).toEqual([]);
  });

  test("권한을 받지 못하면 듣기를 끝내고 설정에서 켜라고 말한다", async ({ page }) => {
    await page.goto(`/child/${CHILD}/home`);
    await mic(page).click();
    await emit(page, { type: "error", reason: "denied" });

    await expect(
      page.getByRole("status").filter({
        hasText: "마이크 권한이 꺼져 있어요. 휴대폰 설정에서 아이캐치의 마이크를 켜 주세요.",
      }),
    ).toBeVisible();
    await expect(listeningLine(page)).toHaveCount(0);
    await expect(mic(page)).toHaveAttribute("aria-pressed", "false");
  });

  test("이 채팅바가 시작하지 않은 듣기가 시작되면 멈춘다", async ({ page }) => {
    // 셸은 듣는데 화면은 꺼져 있는 상태를 남기지 않는다 (`useVoiceInput` 머리말).
    await page.goto(`/child/${CHILD}/home`);
    await expect(mic(page)).toBeEnabled();
    await emit(page, { type: "start" });
    expect(await speechCalls(page)).toEqual(["stop"]);
    await expect(mic(page)).toHaveAttribute("aria-pressed", "false");
  });

  test("04 대화에서 듣는 중에 보내면 듣기를 버리고, 지난 듣기가 끝나기 전에는 새로 듣지 않는다", async ({
    page,
  }) => {
    // 🚨 03 홈은 보내면 04 로 넘어가 채팅바가 새로 서서 이 경로를 못 건다. 보낸 뒤에도 채팅바가 남는 04 에서 건다.
    await page.goto(`/child/${CHILD}/chat`);
    await mic(page).click();
    await emit(page, { type: "result", text: "공원에서 놀았어요", final: false });
    await expect(input(page)).toHaveValue("공원에서 놀았어요");

    await page.getByRole("button", { name: "이 이야기 남기기" }).click();
    await expect(input(page)).toHaveValue("");
    expect(await speechCalls(page)).toEqual(["start", "stop"]);
    await expect(mic(page)).toHaveAttribute("aria-pressed", "false");

    // 지난 듣기의 `end` 가 오기 전에 누르면 아무 일도 없다 — 늦은 글자가 새 글 뒤에 붙지 않게.
    await mic(page).click();
    expect(await speechCalls(page)).toEqual(["start", "stop"]);

    // 그만 듣기 뒤에도 마지막 구간이 한 번 더 온다 (셸 머리말). 비운 입력창을 다시 채우지 않는다.
    await emit(page, { type: "result", text: "공원에서 놀았어요", final: true });
    await expect(input(page)).toHaveValue("");

    // 기다리는 동안 오는 `start` 는 버린 듣기다(셸이 권한을 확인하는 사이에 보냈을 때) — 멈춘다.
    await emit(page, { type: "start" });
    expect(await speechCalls(page)).toEqual(["start", "stop", "stop"]);

    await emit(page, { type: "end" });
    await mic(page).click();
    expect(await speechCalls(page)).toEqual(["start", "stop", "stop", "start"]);
  });
});
