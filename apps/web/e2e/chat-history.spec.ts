import type { Page } from "@playwright/test";

import { CHILD, expect, test } from "./fixtures";
import { browserDaysAgo } from "./time-zone";

/**
 * 04 지난 대화 (#228) — 서버가 남긴 대화가 다시 서는가, 지난 턴에서 막다른 버튼이 사라지는가.
 *
 * `chat_history` 시나리오는 오늘 · 어제 · 사흘 전 대화를 깐다 (`mocks/handlers/chat.ts`). 탭을 새로 여는 것이
 * 곧 "앱을 다시 켠 것" 이다 — 대화 스토어(메모리)는 비어 있고 서버에 남은 것만 있다.
 */

/** 그 한 줄의 답 묶음 (말풍선 하나 = `li` 하나). */
function turn(page: Page, text: string) {
  return page.getByRole("listitem").filter({ hasText: text }).first();
}

test.describe("앱을 다시 켜도 오늘 대화가 보인다", () => {
  test.use({ scenario: "chat_history" });

  test("대화 화면은 서버에 남은 오늘 대화를 그리고, 지난 날은 올려야 붙는다", async ({ page }) => {
    await page.goto(`/child/${CHILD}/chat`);

    await expect(turn(page, "아침에 계란을 잘 먹었어")).toContainText("기록 1건 저장됨");
    // 🚨 열자마자 지난 날을 줄줄이 받지 않는다 — 올리거나 눌러야 붙는다.
    await expect(page.getByText("주말에 물놀이 가기로 했어")).toHaveCount(0);
    await expect(page.getByRole("button", { name: "이전 대화 보기" })).toBeVisible();
  });

  test("03 홈은 서버에 오늘 대화가 있으면 대화로 가는 문을 세운다", async ({ page }) => {
    await page.goto(`/child/${CHILD}/home`);
    await page.getByRole("link", { name: "오늘 대화 이어보기" }).click();
    await page.waitForURL(`**/child/${CHILD}/chat`);
    await expect(turn(page, "아침에 계란을 잘 먹었어")).toBeVisible();
  });
});

test.describe("지난 턴에는 지금 누르면 막다른 버튼이 없다", () => {
  test.use({ scenario: "chat_history" });

  test.beforeEach(async ({ page }) => {
    await page.goto(`/child/${CHILD}/chat`);
    await page.getByRole("button", { name: "이전 대화 보기" }).click();
    await expect(turn(page, "주말에 물놀이 가기로 했어")).toBeVisible();
    await page.getByRole("button", { name: "이전 대화 보기" }).click();
    await expect(turn(page, "블록 놀이를 오래 했어")).toBeVisible();
  });

  test("날짜 구분선이 서고, 다 받으면 더 부르는 문이 사라진다", async ({ page }) => {
    for (const n of [0, 1, 3]) {
      await expect(page.getByRole("region", { name: new RegExp(dayLabel(n)) })).toBeVisible();
    }
    await expect(page.getByRole("button", { name: "이전 대화 보기" })).toHaveCount(0);
  });

  test("🚨 지난 일정 초안은 넣을 수 없다 — 초안은 서버에 남지 않아서 넣었는지 모른다", async ({
    page,
  }) => {
    const pool = turn(page, "주말에 물놀이 가기로 했어");
    await expect(pool).toContainText("그때 찾은 일정");
    await expect(pool).toContainText("물놀이");
    await expect(pool.getByRole("button", { name: "확인하고 넣기" })).toHaveCount(0);
  });

  test('🚨 지난 부분 실패에는 "다시 준비하기" 가 없고, "후보 보기" 는 남는다', async ({ page }) => {
    const dinner = turn(page, "저녁 뭐 먹일지 같이 정해 줘");
    await expect(dinner).toContainText("놀이 쪽은 이번에 처리하지 못했어요.");
    await expect(dinner.getByRole("link", { name: "다시 준비하기" })).toHaveCount(0);
    await expect(dinner.getByRole("link", { name: "후보 보기" })).toHaveAttribute(
      "href",
      /run=r_hist_dinner/,
    );
  });

  test("🚨 지운 기록은 내용 없이 지웠다고만 말한다", async ({ page }) => {
    const blocks = turn(page, "블록 놀이를 오래 했어");
    await expect(blocks).toContainText("이 기록은 지웠어요.");
    await expect(blocks).toContainText("기록 1건 저장됨");
  });

  test("🚨 지난 질문에는 답할 자리가 없다 — 답했으면 답함, 아니면 닫힘", async ({ page }) => {
    await expect(page.getByRole("button", { name: "이 질문에 답하기" })).toHaveCount(0);
    await expect(turn(page, "요즘 기침을 해")).toContainText("아래에 답해 주셨어요.");
    await expect(turn(page, "블록 놀이를 오래 했어")).toContainText(
      "이 질문은 더 이어서 답할 수 없어요.",
    );
    // 답한 줄은 그날처럼 "질문에 대한 답" 으로 서고, 건강 기록도 run 화면과 같은 모양이다.
    const reply = turn(page, "사흘 전부터");
    await expect(reply).toContainText("질문에 대한 답");
    await expect(reply).toContainText("건강");
  });
});

test.describe("위로 올리면 더 이른 대화가 붙는다", () => {
  test.use({ scenario: "chat_history" });

  test("붙는 동안 보던 줄이 제자리에 있다", async ({ page }) => {
    await page.goto(`/child/${CHILD}/chat`);
    await page.getByRole("button", { name: "이전 대화 보기" }).click();
    const cough = page.getByText("요즘 기침을 해");
    await expect(cough).toBeVisible();

    await cough.scrollIntoViewIfNeeded();
    await page.mouse.move(200, 400);
    await page.mouse.wheel(0, -5000);
    await expect(page.getByText("블록 놀이를 오래 했어")).toBeAttached();
    // 🚨 위에 사흘 전 대화가 붙었어도 보던 줄은 화면 안에 그대로 있다 — 맨 위로 튀지 않는다.
    await expect(page.getByText("요즘 기침을 해")).toBeInViewport();
  });
});

test.describe("09 캘린더 하루 보기에서 그날 대화로 들어간다", () => {
  test.use({ scenario: "chat_history" });

  test("대화 수가 서고, 누르면 그날 자리로 열리고 캘린더로 돌아간다", async ({ page }) => {
    const day = browserDaysAgo(3);
    await page.goto(`/child/${CHILD}/calendar?date=${day}`);
    await page.getByRole("link", { name: "대화 2개 보기" }).click();
    await page.waitForURL(`**/child/${CHILD}/chat?date=${day}`);

    await expect(page.getByText("블록 놀이를 오래 했어")).toBeInViewport();
    await expect(page.getByRole("link", { name: "캘린더로 돌아가기" })).toHaveAttribute(
      "href",
      `/child/${CHILD}/calendar?date=${day}`,
    );
    // 오늘까지 이어진 같은 대화다 — 그날만 따로 연 화면이 아니다.
    await expect(page.getByText("아침에 계란을 잘 먹었어")).toBeAttached();
  });
});

test.describe("오늘 대화가 없는 날 캘린더에서 지난 날로 들어간다", () => {
  test.use({ scenario: "chat_history_past" });

  test("🚨 그날을 찾는 동안 빈 상태가 깜빡이지 않는다 — 첫 장(오늘)은 비어 온다", async ({
    page,
  }) => {
    // 빈 상태가 한 번이라도 섰는지 문서 변화를 지켜본다 — 잠깐 섰다 사라지면 단언으로는 못 잡는다.
    await page.addInitScript(() => {
      const seen = () => {
        if (document.body?.textContent?.includes("아래에 한 줄 적으면 여기서 이어져요")) {
          (window as unknown as { emptyShown: boolean }).emptyShown = true;
        }
      };
      new MutationObserver(seen).observe(document, { childList: true, subtree: true });
    });
    const day = browserDaysAgo(3);
    await page.goto(`/child/${CHILD}/calendar?date=${day}`);
    await page.getByRole("link", { name: "대화 2개 보기" }).click();
    await page.waitForURL(`**/child/${CHILD}/chat?date=${day}`);

    await expect(page.getByText("블록 놀이를 오래 했어")).toBeInViewport();
    expect(
      await page.evaluate(() => (window as unknown as { emptyShown?: boolean }).emptyShown),
    ).toBe(undefined);
  });
});

test.describe("run 이 끝나면 대화 목록은 오늘 장만 다시 받는다 (#228 리뷰)", () => {
  test.use({ scenario: "chat_history" });

  test("🚨 올려 둔 지난 날을 다시 받지 않는다 — 무한 목록을 통째로 무효화하면 받아 둔 장을 전부 다시 받는다", async ({
    page,
  }) => {
    await page.goto(`/child/${CHILD}/chat`);
    await page.getByRole("button", { name: "이전 대화 보기" }).click();
    await expect(turn(page, "주말에 물놀이 가기로 했어")).toBeVisible();
    await page.getByRole("button", { name: "이전 대화 보기" }).click();
    await expect(turn(page, "블록 놀이를 오래 했어")).toBeVisible();

    const chatRequests: string[] = [];
    page.on("request", (request) => {
      const url = new URL(request.url());
      if (url.pathname === `/api/v1/children/${CHILD}/chat`) chatRequests.push(url.search);
    });
    await page.getByRole("textbox", { name: "오늘 있었던 일" }).fill("블록 놀이 했어");
    await page.getByRole("button", { name: "이 이야기 남기기" }).click();
    await expect(turn(page, "블록 놀이 했어")).toContainText("이렇게 저장했어요");
    await page.waitForLoadState("networkidle");

    // 오늘 장(쿼리 없음)만 다시 받는다. 전날 장(`date=`)이나 같은 날 뒷장(`cursor=`)은 부르지 않는다.
    expect(chatRequests.length).toBeGreaterThan(0);
    expect(chatRequests.filter((search) => search !== "")).toEqual([]);
    // 지난 날은 그대로 화면에 있다.
    await expect(turn(page, "블록 놀이를 오래 했어")).toBeAttached();
  });
});

test.describe("이 앱에서 보낸 줄과 서버가 남긴 줄", () => {
  test("🚨 같은 run 은 한 번만 선다 — run 이 끝나 서버가 남겨도 말풍선이 둘이 되지 않는다", async ({
    page,
  }) => {
    await page.goto(`/child/${CHILD}/home`);
    await page.getByRole("textbox", { name: "오늘 있었던 일" }).fill("블록 놀이 했어");
    await page.getByRole("button", { name: "이 이야기 남기기" }).click();
    await page.waitForURL(`**/child/${CHILD}/chat`);
    await expect(turn(page, "블록 놀이 했어")).toContainText("이렇게 저장했어요");

    // run 이 끝나면 대화 목록을 다시 받는다 — 받은 뒤에도 하나다.
    await page.waitForLoadState("networkidle");
    await expect(page.getByText("블록 놀이 했어", { exact: true })).toHaveCount(1);
  });

  test("대화가 없는 날의 하루 보기에는 대화로 가는 문이 없다", async ({ page }) => {
    await page.goto(`/child/${CHILD}/calendar?date=${browserDaysAgo(0)}`);
    await expect(page.getByRole("heading", { name: "이날의 일기" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "이날 나눈 대화" })).toHaveCount(0);
  });
});

/** 날짜 구분선의 글자 ("10월 13일"). 요일까지 맞추지 않고 월 · 일만 본다. */
function dayLabel(n: number): string {
  const [, month, day] = browserDaysAgo(n).split("-").map(Number);
  return `${month}월 ${day}일`;
}
