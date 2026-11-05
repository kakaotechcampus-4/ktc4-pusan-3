import type { Page } from "@playwright/test";

import { CHILD, expect, recordApi, test, writes, type ApiCall } from "./fixtures";

/**
 * 흐린 위치 (#593) — 입력 요청 바디에 `location` 이 언제 실리는가.
 *
 * 실제 셸 대신 `window.icatch.location` 을 꽂고 `icatch:location` 으로 답한다 (`push-shell.spec.ts` 와 같은 방식).
 * 셸이 돌려주는 좌표는 일부러 덜 흐린 값이다 — 웹이 둘째 자리로 줄이는지까지 본다.
 *
 * 거는 것: 동의가 없으면 싣지 않고 셸에 읽기도 부탁하지 않는다 · 동의를 켜면 권한 창을 띄운다 · 권한을 거부하면
 * 싣지 않고 10 설정이 다시 묻는 길을 보여 준다 · 허용하면 둘째 자리 좌표를 싣는다.
 * ⚠️ 목 상태는 탭 안에만 산다 — 동의를 켠 뒤로는 `page.goto` 없이 화면 안의 길로 간다.
 */

type Permission = "granted" | "declined" | "denied";

/**
 * 셸 흉내. `permission` 은 권한 창에서 고를 답이다. 셸에 온 부탁은 `__locationSent` 에 쌓는다.
 * `readFails` 면 허용했어도 좌표를 못 읽는다 (위치 서비스 꺼짐 같은 경우).
 */
async function installShell(page: Page, permission: Permission, readFails = false) {
  await page.addInitScript(
    ([answer, fails]) => {
      const sent: string[] = [];
      let state = "prompt";
      const reply = (detail: unknown) =>
        setTimeout(() => window.dispatchEvent(new CustomEvent("icatch:location", { detail })));
      (window as unknown as { __locationSent: string[] }).__locationSent = sent;
      (window as unknown as { icatch: unknown }).icatch = {
        location: {
          available: true,
          check: () => {
            sent.push("check");
            reply({ type: "permission", action: "check", status: state });
          },
          ask: () => {
            sent.push("ask");
            state = answer;
            reply({ type: "permission", action: "ask", status: state });
          },
          read: () => {
            sent.push("read");
            reply(
              state !== "granted"
                ? { type: "unavailable", reason: "not-granted" }
                : fails
                  ? { type: "unavailable", reason: "failed" }
                  : { type: "position", lat: 35.123456, lon: 129.987654 },
            );
          },
          openSettings: () => sent.push("settings"),
        },
      };
    },
    [permission, readFails] as const,
  );
}

/** 셸이 받은 `check` 수. 안내가 권한을 확인했다는 신호다 — "서지 않는다" 는 이 수가 늘어난 뒤에 센다. */
async function checkCount(page: Page) {
  return (await shellRequests(page)).filter((action) => action === "check").length;
}

function shellRequests(page: Page) {
  return page.evaluate(() => (window as unknown as { __locationSent: string[] }).__locationSent);
}

/** 10 설정에서 위치정보 동의를 켠다. 켜면 셸에 권한 창을 부탁한다. */
async function grantLocationConsent(page: Page) {
  await page.goto(`/child/${CHILD}/settings`);
  await page.getByRole("button", { name: "동의하기" }).click();
  await page.getByRole("dialog").getByRole("button", { name: "동의할게요" }).click();
  await expect(page.getByText("동의함")).toBeVisible();
  await expect.poll(() => shellRequests(page)).toContain("ask");
}

/** 홈으로 화면 안의 길로 가서 한 줄을 보낸다. 보낸 입력 요청의 바디를 돌려준다. */
async function sendLine(page: Page, calls: ApiCall[], fromSettings: boolean) {
  if (fromSettings) await page.getByRole("link", { name: "홈으로 돌아가기" }).click();
  else await page.goto(`/child/${CHILD}/home`);
  await page.getByRole("textbox", { name: "오늘 있었던 일" }).fill("공원에서 모래놀이 했대");
  await page.getByRole("button", { name: "이 이야기 남기기" }).click();
  await page.waitForURL(`**/child/${CHILD}/chat`);
  await expect.poll(() => writes(calls, "POST", /\/inputs$/).length).toBe(1);
  return writes(calls, "POST", /\/inputs$/)[0]!.body as Record<string, unknown>;
}

test("🚨 위치 동의가 없으면 휴대폰이 허용했어도 싣지 않는다 · 셸에 읽기도 부탁하지 않는다", async ({
  page,
}) => {
  await installShell(page, "granted");
  const calls = recordApi(page);

  const body = await sendLine(page, calls, false);
  expect(body).not.toHaveProperty("location");
  expect(await shellRequests(page)).not.toContain("read");
});

test("동의는 있는데 권한을 거부하면 싣지 않고, 10 설정이 다시 묻는 길을 보여 준다", async ({
  page,
}) => {
  await installShell(page, "declined");
  const calls = recordApi(page);

  await grantLocationConsent(page);
  await expect(
    page.getByText("휴대폰에서 위치를 허용하지 않았어요.", { exact: false }),
  ).toBeVisible();
  await page.getByRole("button", { name: "위치 권한 허용하기" }).click();
  await expect
    .poll(async () => (await shellRequests(page)).filter((a) => a === "ask").length)
    .toBe(2);

  const body = await sendLine(page, calls, true);
  expect(body).not.toHaveProperty("location");
});

test("더 물을 수 없게 막혔으면 휴대폰 설정을 여는 길을 준다", async ({ page }) => {
  await installShell(page, "denied");

  await grantLocationConsent(page);
  await expect(
    page.getByText("휴대폰 설정에서 아이캐치의 위치 권한을", { exact: false }),
  ).toBeVisible();
  await page.getByRole("button", { name: "휴대폰 설정 열기" }).click();
  await expect.poll(() => shellRequests(page)).toContain("settings");
});

test("동의하고 허용하면 소수점 둘째 자리 좌표를 싣는다", async ({ page }) => {
  await installShell(page, "granted");
  const calls = recordApi(page);

  await grantLocationConsent(page);
  // 허용했으면 안내가 서지 않는다.
  await expect(page.getByRole("button", { name: "위치 권한 허용하기" })).toHaveCount(0);

  const body = await sendLine(page, calls, true);
  expect(body.location).toEqual({ lat: 35.12, lon: 129.99 });
});

test("🚨 동의를 철회하면 다음 한 줄부터 싣지 않는다", async ({ page }) => {
  await installShell(page, "granted");
  const calls = recordApi(page);

  await grantLocationConsent(page);
  await page.getByRole("button", { name: "철회하기" }).click();
  await page.getByRole("dialog").getByRole("button", { name: "철회할게요" }).click();
  await expect(page.getByText("동의하지 않음")).toBeVisible();

  const body = await sendLine(page, calls, true);
  expect(body).not.toHaveProperty("location");
});

/**
 * 놀이 추천 아래 "위치 권한이 꺼져 있어서" 안내 (#638 리뷰). 목의 `agent_suggestions` 는 결과에 놀이 블록이 있다.
 * 🚨 위치 동의를 한 보호자에게만, 아무것도 안 누르면 하루에 한 번까지만 선다.
 */
test.describe("놀이 추천 아래 위치 안내", () => {
  test.use({ scenario: "agent_suggestions" });

  const NOTE = "위치 권한이 꺼져 있어서 근처 장소 없이 준비했어요.";

  async function sendFromChat(page: Page, text: string) {
    await page.getByRole("textbox", { name: "오늘 있었던 일" }).fill(text);
    await page.getByRole("button", { name: "이 이야기 남기기" }).click();
    await expect(
      page.getByRole("listitem").filter({ hasText: text }).getByText("후보 보기").first(),
    ).toBeVisible();
  }

  test("동의했는데 권한을 거절했으면 결과 아래에 서고, 위치 안 쓸래요는 동의를 철회한다", async ({
    page,
  }) => {
    await installShell(page, "declined");
    const calls = recordApi(page);

    await grantLocationConsent(page);
    await sendLine(page, calls, true);
    await expect(page.getByText(NOTE)).toBeVisible();
    await expect(page.getByRole("button", { name: "위치 권한 허용하기" })).toBeVisible();

    await page.getByRole("button", { name: "위치 안 쓸래요" }).click();
    await expect(
      page.getByText("위치를 쓰지 않을게요. 설정에서 언제든 다시 켤 수 있어요."),
    ).toBeVisible();
    expect(writes(calls, "POST", /\/consents$/).at(-1)?.body).toMatchObject({
      scope: "location",
      action: "withdrawn",
    });
  });

  test("🚨 아무것도 안 누르면 그날은 한 번만 선다", async ({ page }) => {
    await installShell(page, "declined");
    const calls = recordApi(page);

    await grantLocationConsent(page);
    await sendLine(page, calls, true);
    await expect(page.getByText(NOTE)).toBeVisible();

    // 🚨 두 번째 줄의 안내가 권한을 확인한 뒤에 센다. 바로 세면 안내가 서기 전이라 늘 통과한다.
    const before = await checkCount(page);
    await sendFromChat(page, "주말에 놀 거리 찾아줘");
    await expect.poll(() => checkCount(page)).toBeGreaterThan(before);
    await expect(page.getByText(NOTE)).toHaveCount(1);
  });

  test("허용은 했는데 좌표를 못 읽어 빠졌으면 서지 않는다 — 꺼져 있다는 말이 거짓이 된다", async ({
    page,
  }) => {
    await installShell(page, "granted", true);
    const calls = recordApi(page);

    await grantLocationConsent(page);
    const before = await checkCount(page);
    const body = await sendLine(page, calls, true);
    expect(body).not.toHaveProperty("location");
    await expect.poll(() => checkCount(page)).toBeGreaterThan(before);
    // 안내 자체가 없어야 한다 — 경고 문장도, 눌러서 풀렸을 때의 문장도.
    await expect(page.getByText(NOTE)).toHaveCount(0);
    await expect(page.getByText("위치를 허용했어요", { exact: false })).toHaveCount(0);
  });

  // ⚠️ 동의를 안 한 보호자는 줄이 `withoutLocation` 이 아니라 안내 자체가 그려지지 않는다 — 기다릴 신호가 없어
  //    e2e 로는 "없다" 를 증명할 수 없다. `conversation.test.ts` 의 `withoutLocation` 이 동의 없음을 건다.
});
