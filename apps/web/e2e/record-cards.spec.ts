import type { Page } from "@playwright/test";

import { CHILD, expect, expectNoGate, recordApi, test, writes } from "./fixtures";

/**
 * 04 대화의 조회 · 삭제 카드 (#535) — 대화로 찾거나 지울 때 **저장된 행을 그대로** 보이고, 삭제는 버튼으로만 한다.
 *
 * 🚨 지키는 것 (`docs/web/chat-run-v1.md` §5)
 * - AI 는 지우지 않는다 — 카드가 뜬 것만으로는 쓰기가 0건이다. 두 번 눌러야 확정이 나간다
 * - 지우는 것은 **체크한 것만**이고 확정에는 Idempotency-Key 가 붙는다
 * - 한 줄 요약은 체크한 수가 아니라 응답의 `result` 로 말한다
 * - 승인 게이트 표식(approve · caution)이 서지 않는다 — 게이트 문구는 별도 문서 PR 에서 고친다
 *
 * 목의 제안 (`mocks/handlers/delete-proposals.ts`): 식사 40건(잘림 · 간식 13건은 AI 가 안 고름) · 놀이 12건 ·
 * 일정 2건(하나도 안 고름). 기본 체크는 식사 27건 + 놀이 12건 = 39건이다.
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

test.describe("조회 카드 — 보기 전용", () => {
  test.use({ scenario: "found_tabs" });

  test("찾은 기록 · 일정이 종류별 탭으로 서고, 저장 0건이어도 '찾지 못했어요' 를 세우지 않는다", async ({
    page,
  }) => {
    const calls = recordApi(page);
    await sendFromHome(page, "이번주 일정과 기록 보여줘");
    const reply = turn(page, "이번주 일정과 기록 보여줘");

    await expect(reply.getByRole("heading", { name: "찾은 기록 6건, 일정 2건" })).toBeVisible();
    const tabs = reply.getByRole("tablist", { name: "찾은 기록과 일정 종류" });
    // 🚨 숫자는 "3" 이 아니라 "3건" 으로 읽힌다 (탭의 `countLabel`).
    for (const name of ["식사 3건", "교육 1건", "놀이 2건", "일정 2건"]) {
      await expect(tabs.getByRole("tab", { name, exact: true })).toBeVisible();
    }
    await expect(reply.getByText("어린이집 점심 미역국 다 먹었대")).toBeVisible();
    await expect(reply.getByText(/아이에 관한 기록은 찾지 못했어요/)).toHaveCount(0);

    await tabs.getByRole("tab", { name: /일정/ }).click();
    await expect(reply.getByText("예방접종")).toBeVisible();
    // 준비물은 읽기만 한다 — 체크박스가 없다.
    await expect(reply.getByText("아기수첩")).toBeVisible();
    await expect(reply.getByRole("checkbox")).toHaveCount(0);

    await expectNoGate(page);
    await page.screenshot({ path: "test-results/record-cards-found.png", fullPage: true });
    expect(writes(calls).filter((c) => c.path !== `/children/${CHILD}/inputs`)).toEqual([]);
  });
});

test.describe("삭제 카드 — 체크한 것만 버튼으로 지운다 (§2 실행)", () => {
  test.use({ scenario: "delete_proposal" });

  const SAID = "저번주 식사랑 놀이 기록 중에 간식은 빼고 지워줘";

  test("AI 가 고른 것이 기본 체크고, 두 번 눌러야 확정이 나가며, 요약은 응답의 건수다", async ({
    page,
  }) => {
    const calls = recordApi(page);
    await sendFromHome(page, SAID);
    const reply = turn(page, SAID);

    await expect(reply.getByRole("heading", { name: "지울 기록과 일정 고르기" })).toBeVisible();
    const tabs = reply.getByRole("tablist", { name: "지울 기록과 일정 종류" });
    // 🚨 "27/40" 이 아니라 "40건 중 27건 고름" 으로 읽힌다 (탭의 `countLabel`).
    for (const name of [
      "식사 40건 중 27건 고름",
      "놀이 12건 중 12건 고름",
      "일정 2건 중 0건 고름",
    ]) {
      await expect(tabs.getByRole("tab", { name, exact: true })).toBeVisible();
    }
    await expect(tabs.getByRole("tab", { name: /식사/ })).toContainText("27/40");
    await expect(reply.getByText(/최근 40건만 보여드렸어요/)).toBeVisible();
    await expect(reply.getByText("요청에 맞는 기록 27건")).toBeVisible();
    // 🚨 AI 가 안 고른 간식은 "나머지" 로 접혀 있다.
    await expect(reply.getByRole("button", { name: "나머지 기록 13건 보기" })).toBeVisible();
    await expect(reply.getByText("간식으로 요거트 (3)")).toHaveCount(0);

    await expectNoGate(page);
    await page.screenshot({ path: "test-results/record-cards-delete.png", fullPage: true });

    // 🚨 카드가 뜬 것만으로는 아무것도 지우지 않는다. 한 번 누르면 묻기만 한다.
    await reply.getByRole("button", { name: "39건 삭제하기" }).click();
    const ask = reply.getByRole("group", { name: "삭제 확인" });
    await expect(ask.getByText("식사 27건, 놀이 12건을 지울까요?")).toBeVisible();
    expect(writes(calls, "POST", /delete-proposals/)).toEqual([]);
    await page.screenshot({ path: "test-results/record-cards-confirm.png", fullPage: true });

    await ask.getByRole("button", { name: "삭제" }).click();
    await expect(reply.getByText("식사 27건, 놀이 12건을 삭제했어요.")).toBeVisible();
    await expect(reply.getByRole("checkbox")).toHaveCount(0);

    const confirmed = writes(calls, "POST", /\/delete-proposals\/[^/]+\/confirm$/);
    expect(confirmed).toHaveLength(1);
    expect(confirmed[0].idempotencyKey).toBeTruthy();
    expect((confirmed[0].body as { ids: string[] }).ids).toHaveLength(39);
  });

  test("체크를 풀어도 행이 묶음 사이를 옮겨 다니지 않고, 푼 것은 보내지 않는다", async ({
    page,
  }) => {
    const calls = recordApi(page);
    await sendFromHome(page, SAID);
    const reply = turn(page, SAID);

    const first = reply.getByRole("checkbox", { name: /아침에 바나나 하나 \(1\)/ });
    await expect(first).toBeChecked();
    await first.uncheck({ force: true });
    await expect(first).not.toBeChecked();
    // 묶음은 처음 체크로 고정이다 — 푼 행은 그 자리에 남는다.
    await expect(reply.getByText("요청에 맞는 기록 27건")).toBeVisible();
    await expect(
      reply.getByRole("tab", { name: "식사 40건 중 26건 고름", exact: true }),
    ).toBeVisible();

    // AI 가 안 고른 탭은 나머지가 처음부터 펼쳐져 있다.
    await reply.getByRole("tab", { name: /일정/ }).click();
    await expect(reply.getByRole("button", { name: "나머지 일정 2건 접기" })).toBeVisible();
    await reply.getByRole("checkbox", { name: /소아과 예약/ }).check({ force: true });

    await reply.getByRole("button", { name: "39건 삭제하기" }).click();
    await reply
      .getByRole("group", { name: "삭제 확인" })
      .getByRole("button", { name: "삭제" })
      .click();
    await expect(reply.getByText("식사 26건, 놀이 12건, 일정 1건을 삭제했어요.")).toBeVisible();

    const [confirmed] = writes(calls, "POST", /\/confirm$/);
    const ids = (confirmed!.body as { ids: string[] }).ids;
    expect(ids).not.toContain("dp_f1");
    expect(ids).toContain("dp_ev1");
  });

  test("다 풀면 버튼이 막히고 이유를 말한다 · 취소는 아무것도 지우지 않는다", async ({ page }) => {
    const calls = recordApi(page);
    await sendFromHome(page, SAID);
    const reply = turn(page, SAID);

    await reply.getByRole("button", { name: "전체 해제" }).click();
    await reply.getByRole("tab", { name: /놀이/ }).click();
    await reply.getByRole("button", { name: "전체 해제" }).click();
    await expect(reply.getByRole("button", { name: "고른 것이 없어요" })).toBeDisabled();

    await reply.getByRole("button", { name: "취소" }).click();
    await expect(reply.getByText("삭제를 취소했어요.")).toBeVisible();
    expect(writes(calls, "POST", /\/confirm$/)).toEqual([]);
    expect(writes(calls, "POST", /\/cancel$/)).toHaveLength(1);
  });

  test("🚨 지운 카드는 홈에 갔다 와도 접힌 채다 — 체크와 버튼이 다시 서지 않는다", async ({
    page,
  }) => {
    const calls = recordApi(page);
    await sendFromHome(page, SAID);
    const reply = turn(page, SAID);
    await reply.getByRole("button", { name: "39건 삭제하기" }).click();
    await reply
      .getByRole("group", { name: "삭제 확인" })
      .getByRole("button", { name: "삭제" })
      .click();
    await expect(reply.getByText("식사 27건, 놀이 12건을 삭제했어요.")).toBeVisible();

    // 화면 안 링크로만 옮겨 다닌다 — 새로 열면 대화 스토어와 목이 같이 빈다.
    await page.getByRole("link", { name: "홈으로 돌아가기" }).click();
    await page.getByRole("link", { name: "오늘 대화 이어보기" }).click();
    await page.waitForURL(`**/child/${CHILD}/chat`);

    const again = turn(page, SAID);
    await expect(again.getByText("식사 27건, 놀이 12건을 삭제했어요.")).toBeVisible();
    await expect(again.getByRole("checkbox")).toHaveCount(0);
    await expect(again.getByRole("button", { name: /삭제하기/ })).toHaveCount(0);
    expect(writes(calls, "POST", /\/confirm$/)).toHaveLength(1);
  });
});

test.describe("삭제 카드 — 띄워 둔 채 만료", () => {
  test.use({ scenario: "delete_proposal_expiring" });

  test("expires_at 이 지나면 화면이 닫힌 카드로 바꾸고, 누를 길이 없다", async ({ page }) => {
    // 목도 같은 페이지 시계를 본다(MSW 브라우저 모드) — 시간을 넘기면 서버 쪽 만료도 같이 지난다.
    await page.clock.install();
    const calls = recordApi(page);
    const said = "어제 간식 기록 지워줘";
    await sendFromHome(page, said);
    const reply = turn(page, said);
    await expect(reply.getByRole("button", { name: "39건 삭제하기" })).toBeVisible();

    await page.clock.fastForward(31_000);
    await expect(reply.getByText("시간이 지나 삭제 확인이 닫혔어요.")).toBeVisible();
    // 🚨 화면 시계로 접은 것이라 "아무것도 지우지 않았어요" 라고 단정하지 않는다.
    await expect(
      reply.getByText("이 확인으로는 더 지울 수 없어요. 지우려면 다시 말씀해 주세요."),
    ).toBeVisible();
    await expect(reply.getByRole("checkbox")).toHaveCount(0);
    expect(writes(calls, "POST", /delete-proposals/)).toEqual([]);
  });
});

test.describe("삭제 카드 — 넓은 화면", () => {
  test.use({ scenario: "delete_proposal", viewport: { width: 1118, height: 905 } });

  test("🚨 스크롤 칸 안의 숨긴 체크 input 이 문서를 늘려 화면이 빈 곳으로 내려가지 않는다", async ({
    page,
  }) => {
    await sendFromHome(page, "ff");
    const reply = turn(page, "ff");
    // 대화는 결과가 오면 바닥으로 따라 내린다 — 문서가 늘었으면 그 바닥이 빈 공간이라 버튼이 화면 밖이다.
    await expect(reply.getByRole("button", { name: "39건 삭제하기" })).toBeInViewport();
    const overflow = await page.evaluate(
      () =>
        document.documentElement.scrollHeight -
        (document.querySelector("main")!.getBoundingClientRect().bottom + window.scrollY),
    );
    expect(overflow).toBeLessThan(300);
  });
});
