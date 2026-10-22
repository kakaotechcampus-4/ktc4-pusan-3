import type { Page } from "@playwright/test";

import { CHILD, expect, recordApi, test } from "./fixtures";
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

    await expect(turn(page, "아침에 계란을 잘 먹었어")).toContainText("기록 2건 저장됨");
    // 🚨 열자마자 지난 날을 줄줄이 받지 않는다 — 올리거나 눌러야 붙는다.
    await expect(page.getByText("주말에 물놀이 가기로 했어")).toHaveCount(0);
    await expect(page.getByRole("button", { name: "이전 대화 보기" })).toBeVisible();
  });

  test("03 홈은 서버에 오늘 대화가 있으면 대화로 가는 문을 세운다 — 열린 질문이 있으면 그 이름으로", async ({
    page,
  }) => {
    await page.goto(`/child/${CHILD}/home`);
    // 오늘 대화에 서버가 아직 답할 수 있다고 한 질문이 있다 (`reply_open`) — 스토어는 비어 있어도 그 문이다.
    await page.getByRole("link", { name: "답을 기다리는 질문이 있어요" }).click();
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

  test("🚨 보관함에 없는 지난 일정 초안은 넣을 수 없다 — 넣었는지 모른다", async ({ page }) => {
    const pool = turn(page, "주말에 물놀이 가기로 했어");
    await expect(pool).toContainText("그때 찾은 일정");
    await expect(pool).toContainText("물놀이");
    await expect(pool.getByRole("button", { name: "확인하고 넣기" })).toHaveCount(0);
  });

  test('🚨 지난 부분 실패에는 "다시 준비하기" 가 없고, 고를 후보가 남은 결과에만 "후보 보기" 가 선다', async ({
    page,
  }) => {
    const dinner = turn(page, "저녁 뭐 먹일지 같이 정해 줘");
    await expect(dinner).toContainText("놀이 쪽은 이번에 처리하지 못했어요.");
    await expect(dinner.getByRole("link", { name: "다시 준비하기" })).toHaveCount(0);
    // 하나는 채택했다 — 05 에서 보인다.
    await expect(dinner.getByRole("link", { name: "후보 보기" })).toHaveAttribute(
      "href",
      /run=r_hist_dinner/,
    );
    // 고르지 않은 채 다 지났다 — 눌러도 "이어서 할 수 없어요" 뿐이라 세우지 않는다.
    const play = turn(page, "주말에 뭐 하고 놀지 골라 줘");
    await expect(play).toContainText("지금은 고를 수 있는 후보가 없어요.");
    await expect(play.getByRole("link", { name: "후보 보기" })).toHaveCount(0);
  });

  test("🚨 지운 기록은 내용 없이 지웠다고만 말한다", async ({ page }) => {
    const blocks = turn(page, "블록 놀이를 오래 했어");
    await expect(blocks).toContainText("이 기록은 지웠어요.");
    await expect(blocks).toContainText("기록 1건 저장됨");
  });

  test("🚨 고친 기록은 기록 탭처럼 무엇으로 고쳤는지 말한다", async ({ page }) => {
    const breakfast = turn(page, "아침에 계란을 잘 먹었어");
    await expect(breakfast).toContainText("잘못된 기록으로 고친 기록");
    await expect(breakfast).toContainText("기록 2건 저장됨");
  });

  test("🚨 닫힌 지난 질문에는 답할 자리가 없다 — 답했으면 답함, 아니면 닫힘", async ({ page }) => {
    // 서버가 아직 열려 있다고 한 오늘 질문 하나만 답할 수 있다 (`reply_open`).
    await expect(page.getByRole("button", { name: "이 질문에 답하기" })).toHaveCount(1);
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

test.describe("앱을 다시 켜도 15분 안이면 질문에 이어서 답한다 (reply_open)", () => {
  test.use({ scenario: "chat_history" });

  test("서버가 열려 있다고 한 질문은 답하기가 서고, 답은 reply_to 로 그 run 을 가리킨다", async ({
    page,
  }) => {
    const calls = recordApi(page);
    await page.goto(`/child/${CHILD}/chat`);
    const nap = turn(page, "낮잠을 짧게 잤어");
    await nap.getByRole("button", { name: "이 질문에 답하기" }).click();
    await expect(page.getByText("위 질문에 답하는 중")).toBeVisible();

    await page.getByRole("textbox", { name: "오늘 있었던 일" }).fill("한 시간쯤");
    await page.getByRole("button", { name: "이 이야기 남기기" }).click();
    await expect(turn(page, "한 시간쯤")).toContainText("질문에 대한 답");

    const sent = calls.find((c) => c.method === "POST" && c.path === `/children/${CHILD}/inputs`);
    // 🚨 원문을 다시 보내지 않고 어느 run 에 대한 답인지만 싣는다.
    expect(sent?.body).toMatchObject({ text: "한 시간쯤", reply_to: "r_hist_nap" });
    await expect(nap).toContainText("아래에 답해 주셨어요.");
  });
});

test.describe("탭이 살아 있으면 지난 턴의 초안도 이어서 넣는다", () => {
  test.use({ scenario: "chat_history" });

  test("새로고침해도 보관함에 남은 초안이면 확인하고 넣기가 선다", async ({ page }) => {
    await page.goto(`/child/${CHILD}/chat`);
    // 목은 일정 낱말이 든 한 줄에 물놀이 초안(어제 지난 턴과 같은 초안)을 낸다 — 보관함에 얹힌다.
    await page.getByRole("textbox", { name: "오늘 있었던 일" }).fill("수영장 가기로 했어");
    await page.getByRole("button", { name: "이 이야기 남기기" }).click();
    await expect(
      turn(page, "수영장 가기로 했어").getByRole("button", { name: "확인하고 넣기" }),
    ).toBeVisible();

    // 새로고침 — 대화 스토어와 목은 비지만 초안 보관함(sessionStorage)은 남는다.
    await page.reload();
    await page.getByRole("button", { name: "이전 대화 보기" }).click();
    const pool = turn(page, "주말에 물놀이 가기로 했어");
    await pool.getByRole("button", { name: "확인하고 넣기" }).click();
    // 제목은 입력칸 값이라 준비물로 같은 초안인지 본다.
    await expect(page.getByRole("dialog", { name: "찾은 일정" })).toContainText("수영복");
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

  test("🚨 그날 한 장만 받아 연다 — 오늘부터 하루씩 이어 받지 않는다", async ({ page }) => {
    const day = browserDaysAgo(3);
    await page.goto(`/child/${CHILD}/calendar?date=${day}`);
    const chatRequests: string[] = [];
    page.on("request", (request) => {
      const url = new URL(request.url());
      if (url.pathname === `/api/v1/children/${CHILD}/chat`) chatRequests.push(url.search);
    });
    await page.getByRole("link", { name: "대화 3개 보기" }).click();
    await page.waitForURL(`**/child/${CHILD}/chat?date=${day}`);

    await expect(page.getByText("블록 놀이를 오래 했어")).toBeInViewport();
    await page.waitForLoadState("networkidle");
    expect(chatRequests).toEqual([`?date=${day}`]);
    await expect(page.getByRole("link", { name: "캘린더로 돌아가기" })).toHaveAttribute(
      "href",
      `/child/${CHILD}/calendar?date=${day}`,
    );
  });

  test("아래로 내리면 다음 날이 붙고, 오늘까지 오면 문이 사라진다", async ({ page }) => {
    const day = browserDaysAgo(3);
    await page.goto(`/child/${CHILD}/chat?date=${day}`);
    await expect(page.getByText("블록 놀이를 오래 했어")).toBeVisible();
    await expect(page.getByText("아침에 계란을 잘 먹었어")).toHaveCount(0);

    await page.getByRole("button", { name: "다음 대화 보기" }).click();
    await expect(page.getByText("요즘 기침을 해")).toBeVisible();
    await page.getByRole("button", { name: "다음 대화 보기" }).click();
    await expect(page.getByText("아침에 계란을 잘 먹었어")).toBeVisible();
    await expect(page.getByRole("button", { name: "다음 대화 보기" })).toHaveCount(0);
  });

  test("🚨 그날 화면에서 한 줄 보내면 오늘 대화로 넘어간다 — 며칠을 건너뛰고 붙지 않는다", async ({
    page,
  }) => {
    await page.goto(`/child/${CHILD}/chat?date=${browserDaysAgo(3)}`);
    await expect(page.getByText("블록 놀이를 오래 했어")).toBeVisible();
    await page.getByRole("textbox", { name: "오늘 있었던 일" }).fill("블록 놀이 했어");
    await page.getByRole("button", { name: "이 이야기 남기기" }).click();

    await page.waitForURL(new RegExp(`/child/${CHILD}/chat$`));
    await expect(turn(page, "블록 놀이 했어")).toContainText("이렇게 저장했어요");
    await expect(page.getByRole("link", { name: "홈으로 돌아가기" })).toBeVisible();
  });
});

test.describe("이 앱에서 보낸 줄이 있는 채로 캘린더에서 들어간다", () => {
  test.use({ scenario: "chat_history" });

  test("🚨 그날을 받기 전에 이 앱의 줄을 먼저 세우지 않는다 — 섰다가 사라지면 그날이 아니라 오늘이 보인다", async ({
    page,
  }) => {
    await page.goto(`/child/${CHILD}/chat`);
    await page.getByRole("textbox", { name: "오늘 있었던 일" }).fill("블록 놀이 했어");
    await page.getByRole("button", { name: "이 이야기 남기기" }).click();
    await expect(turn(page, "블록 놀이 했어")).toContainText("이렇게 저장했어요");

    // 화면 안 링크로만 옮겨 다닌다 — 새로 열면 대화 스토어가 빈다.
    await page.getByRole("link", { name: "홈으로 돌아가기" }).click();
    await page.getByRole("link", { name: "캘린더" }).click();
    await page.waitForURL(`**/child/${CHILD}/calendar**`);
    // 그날(오늘)의 서버 턴보다 이 앱의 줄이 먼저 섰는지 문서 변화로 지켜본다.
    await page.evaluate(() => {
      new MutationObserver(() => {
        const text = document.body.textContent ?? "";
        if (text.includes("블록 놀이 했어") && !text.includes("낮잠을 짧게 잤어")) {
          (window as unknown as { liveFirst: boolean }).liveFirst = true;
        }
      }).observe(document.body, { childList: true, subtree: true, characterData: true });
    });
    await page.getByRole("link", { name: /대화 \d+개 보기/ }).click();
    await page.waitForURL(`**/child/${CHILD}/chat?date=**`);

    await expect(page.getByText("낮잠을 짧게 잤어")).toBeVisible();
    // 오늘까지 받았으니 이 앱의 줄도 선다 — 다만 그날의 서버 턴과 같이 선다.
    await expect(page.getByText("블록 놀이 했어", { exact: true })).toBeVisible();
    expect(
      await page.evaluate(() => (window as unknown as { liveFirst?: boolean }).liveFirst),
    ).toBe(undefined);
  });
});

test.describe("오늘 대화가 없는 날 캘린더에서 지난 날로 들어간다", () => {
  test.use({ scenario: "chat_history_past" });

  test("🚨 빈 상태가 깜빡이지 않는다 — 첫 장이 그날이다", async ({ page }) => {
    // 빈 상태가 한 번이라도 섰는지 문서 변화를 지켜본다 — 잠깐 섰다 사라지면 단언으로는 못 잡는다.
    await page.addInitScript(() => {
      const seen = () => {
        const text = document.body?.textContent ?? "";
        if (text.includes("여기서 이어져요") || text.includes("이날은 나눈 대화가 없어요")) {
          (window as unknown as { emptyShown: boolean }).emptyShown = true;
        }
      };
      new MutationObserver(seen).observe(document, { childList: true, subtree: true });
    });
    const day = browserDaysAgo(3);
    await page.goto(`/child/${CHILD}/calendar?date=${day}`);
    await page.getByRole("link", { name: "대화 3개 보기" }).click();
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
