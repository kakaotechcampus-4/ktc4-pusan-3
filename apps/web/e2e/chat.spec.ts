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
    // 숨은 장도 마운트돼 있어서(넘겨도 제출 상태가 남게) 보이는 것만 본다.
    await expect(sheet.getByText("아직 저장되지 않았어요").filter({ visible: true })).toBeVisible();
    expect(writes(calls, "POST", /\/events$/)).toEqual([]);

    await sheet.getByRole("button", { name: "확인했어요, 캘린더에 넣을게요" }).click();
    await expect(sheet.getByText("2건 중 1건 넣었어요.")).toBeVisible();
    const submitted = writes(calls, "POST", /\/children\/c1\/events$/);
    expect(submitted).toHaveLength(1);
    expect(submitted[0].idempotencyKey).toBeTruthy();
  });

  test("🚨 넣고 다음 장으로 넘겼다 돌아와도 '넣었어요' 그대로다 — 다시 넣을 길이 없고 쓰기는 한 번이다 (#537 리뷰)", async ({
    page,
  }) => {
    const calls = recordApi(page);
    await sendFromHome(page, "지어낸 한 줄");
    await turn(page, "지어낸 한 줄").getByRole("button", { name: "확인하고 넣기" }).click();
    const sheet = page.getByRole("dialog", { name: "찾은 일정" });

    await sheet.getByRole("button", { name: "확인했어요, 캘린더에 넣을게요" }).click();
    await expect(sheet.getByText("2건 중 1건 넣었어요.")).toBeVisible();

    await sheet.getByRole("button", { name: "다음" }).click();
    await expect(sheet.getByText("2건 중 2번째")).toBeVisible();
    await sheet.getByRole("button", { name: "이전" }).click();
    await expect(sheet.getByText("2건 중 1번째")).toBeVisible();

    // 넘기면서 카드가 처음 상태로 되살아나면 새 키로 한 번 더 넣을 수 있었다 (일정 두 건).
    await expect(
      sheet.getByText("넣었어요", { exact: true }).filter({ visible: true }),
    ).toBeVisible();
    await expect(sheet.getByText("아직 저장되지 않았어요").filter({ visible: true })).toHaveCount(
      0,
    );
    expect(writes(calls, "POST", /\/children\/c1\/events$/)).toHaveLength(1);
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

  test.describe("guidance_growth — 키·몸무게는 채팅으로 저장하지 않는다 (#625)", () => {
    test.use({ scenario: "guidance_growth" });

    test("저장 0건이라고 말하고, 직접 입력하는 곳은 11-1 성장 기록이다", async ({ page }) => {
      await sendFromHome(page, "몸무게는 1톤");
      const reply = turn(page, "몸무게는 1톤");
      await expect(reply.getByRole("heading", { name: "적어주신 말을 확인했어요" })).toBeVisible();
      await expect(reply.getByText("키·몸무게", { exact: true })).toBeVisible();
      await expect(reply.getByRole("link", { name: "직접 입력하러 가기" })).toHaveAttribute(
        "href",
        `/child/${CHILD}/profile/growth`,
      );
      await expect(reply.getByText("이렇게 저장했어요")).toHaveCount(0);
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

test.describe("도메인 Agent 결과는 답 묶음 안의 블록이다 (#227)", () => {
  test.describe("agent_readout — 추천 없는 결과", () => {
    test.use({ scenario: "agent_readout" });

    test("성장 추이와 0개 안내가 도착 순서대로 서고, 기록을 못 찾았다고 말하지 않는다", async ({
      page,
    }) => {
      await sendFromHome(page, "잘 크고 있어?");
      const reply = turn(page, "잘 크고 있어?");

      const delta = reply.getByText("6개월간 키 3.1cm · 몸무게 0.8kg 늘었어요.", { exact: false });
      const empty = reply.getByText("조건에 맞는 추천이 없어요.");
      await expect(empty).toBeVisible();
      await expect(delta).toBeVisible();
      // 🚨 잰 기록은 줄마다 선다 — 줄바꿈을 지우면 숫자가 한 덩어리로 붙는다.
      await expect(delta).toHaveCSS("white-space", "pre-line");
      // 🚨 도착한 순서 그대로다 (성장 → 식사). 재정렬하지 않는다 (#215).
      const growthBox = await delta.boundingBox();
      const foodBox = await empty.boundingBox();
      expect(growthBox!.y).toBeLessThan(foodBox!.y);

      // 🚨 식사 결과가 둘 왔지만(급식 갱신 + 추천) 보일 것이 있는 쪽만 블록이 된다.
      await expect(reply.getByText("식사", { exact: true })).toHaveCount(1);
      await expect(reply.getByText("성장", { exact: true })).toHaveCount(1);

      // 🚨 요청형 한 줄은 저장할 관찰이 없는 것이 정상이다 — Agent 결과가 이미 답했다.
      await expect(reply.getByRole("heading", { name: "적어주신 말을 확인했어요" })).toBeVisible();
      await expect(
        reply.getByText("아이에 관한 기록은 찾지 못했어요", { exact: false }),
      ).toHaveCount(0);
      // 🚨 성장 추이에 평가 표현을 붙이지 않는다 (최상위 §2 키 · 몸무게).
      await expect(reply.getByText(/또래|빠르|느리|정상|백분위/)).toHaveCount(0);
    });
  });

  test.describe("agent_question — Agent 가 되묻는 한 줄", () => {
    test.use({ scenario: "agent_question" });

    test("🚨 답할 자리를 열지 않는다 — reply_to 로 보내면 400 이다 (#246)", async ({ page }) => {
      await sendFromHome(page, "놀이 추천해줘");
      const reply = turn(page, "놀이 추천해줘");

      await expect(
        reply.getByText("요즘 주로 집 안에서 노는지 바깥에서 노는지 알려주시겠어요?"),
      ).toBeVisible();
      await expect(reply.getByText("놀이", { exact: true })).toBeVisible();
      await expect(page.getByRole("button", { name: "이 질문에 답하기" })).toHaveCount(0);
      await expect(page.getByText("위 질문에 답하는 중")).toHaveCount(0);
    });
  });

  test.describe("agent_suggestions — 추천이 든 결과", () => {
    test.use({ scenario: "agent_suggestions" });

    test("후보 수 · 이유 · 또래 기준 표시가 서고, 후보 보기는 그 run 의 05 로 간다", async ({
      page,
    }) => {
      await sendFromHome(page, "저녁이랑 놀이 추천해줘");
      const reply = turn(page, "저녁이랑 놀이 추천해줘");

      await expect(reply.getByText("후보 3개")).toBeVisible();
      await expect(reply.getByText("아이 기록 4건을 보고 골랐어요.")).toBeVisible();
      // 🚨 일반 묶음은 또래 기준임과 쌓인 기록 수를 그대로 적는다 (최상위 §2)
      await expect(reply.getByText("또래 기준 일반 추천 · 쌓인 기록 1건")).toBeVisible();
      // 🚨 개수 안내는 "후보 N개" 가 대신하고, 날씨 안내는 그대로 선다
      await expect(reply.getByText("조건에 맞는 추천을 1개 준비했어요.")).toHaveCount(0);
      await expect(
        reply.getByText("오늘은 바깥 활동이 어려운 날이라 실내 놀이만 골랐어요."),
      ).toBeVisible();
      await expect(
        reply.getByText("아이에 관한 기록은 찾지 못했어요", { exact: false }),
      ).toHaveCount(0);

      const links = reply.getByRole("link", { name: "후보 보기" });
      await expect(links).toHaveCount(2);
      expect(await links.first().getAttribute("href")).toMatch(
        /^\/child\/c1\/suggestions\?agents=food&run=.+/,
      );
      await links.first().click();
      await expect(page).toHaveURL(/\/child\/c1\/suggestions\?agents=food&run=/);
    });
  });

  test.describe("agent_partial — 한 Agent 는 결과, 다른 Agent 는 실패", () => {
    test.use({ scenario: "agent_partial" });

    test("블록과 부분 결과가 한 묶음에 서고, 저장하지 않은 것을 저장했다고 하지 않는다", async ({
      page,
    }) => {
      await sendFromHome(page, "저녁 추천해줘");
      const reply = turn(page, "저녁 추천해줘");

      await expect(reply.getByText("조건에 맞는 추천이 없어요.")).toBeVisible();
      await expect(
        reply.getByText("놀이 쪽은 이번에 처리하지 못했어요.", { exact: true }),
      ).toBeVisible();
      await expect(reply.getByText("저장한 기록은 그대로", { exact: false })).toHaveCount(0);
      await expect(reply.getByText("읽지 못했어요")).toHaveCount(0);
      // 실패한 추천 Agent 를 그 run 의 처음 맥락으로 다시 준비한다 (#249)
      const retry = reply.getByRole("link", { name: "다시 준비하기" });
      await expect(retry).toHaveAttribute(
        "href",
        /^\/child\/c1\/suggestions\?agents=activity&run=.+/,
      );
    });
  });
});

test.describe("저장한 뒤 끊긴 run 은 실패가 아니다 (#271)", () => {
  test.use({ scenario: "interrupted" });

  test("받은 기록은 그대로 서고, 다시 시도 없이 다시 보내지 않아도 된다고 말한다", async ({
    page,
  }) => {
    await sendFromHome(page, "그림 그렸어");
    const reply = turn(page, "그림 그렸어");
    await expect(reply.getByRole("heading", { name: "이렇게 저장했어요" })).toBeVisible();
    await expect(reply.getByText("기록 1건 저장됨")).toBeVisible();
    await expect(reply.getByText("나머지는 마무리하지 못했어요")).toBeVisible();
    await expect(
      reply.getByText("위 기록은 그대로 저장됐어요. 같은 말을 다시 보내지 않아도 돼요."),
    ).toBeVisible();
    // 🚨 새 키로 다시 보내면 두 번 저장된다 — 실패 화면의 두 길을 세우지 않는다.
    await expect(reply.getByRole("button", { name: "다시 시도" })).toHaveCount(0);
    await expect(reply.getByRole("button", { name: "고쳐 쓰기" })).toHaveCount(0);
    await expect(reply.getByText("읽지 못했어요")).toHaveCount(0);
  });

  test('받은 기록이 없어도 "저장하지 않았어요" 라고 말하지 않는다', async ({ page }) => {
    await sendFromHome(page, "주말에 소풍 가기");
    const reply = turn(page, "주말에 소풍 가기");
    await expect(reply.getByRole("heading", { name: "적어주신 내용은 반영했어요" })).toBeVisible();
    await expect(reply.getByText("나머지는 마무리하지 못했어요")).toBeVisible();
    await expect(
      reply.getByText("반영된 내용은 기록 탭이나 달력에서 확인할 수 있어요."),
    ).toBeVisible();
    await expect(reply.getByText("저장하지 않았어요", { exact: false })).toHaveCount(0);
    await expect(reply.getByText("이렇게 이해했어요")).toHaveCount(0);
    await expect(reply.getByRole("button", { name: "다시 시도" })).toHaveCount(0);
  });
});

test.describe("결과를 못 받은 줄을 채널이 지워진 뒤 다시 보낸다 (#538)", () => {
  test.describe("replay_finished — 서버는 끝냈고 대화 기록이 있다", () => {
    test.use({ scenario: "replay_finished" });

    test("SSE 를 다시 열지 않고 저장된 결과가 그 줄에 서며, 다시 보내는 길이 없다", async ({
      page,
    }) => {
      const calls = recordApi(page);
      await sendFromHome(page, "지어낸 한 줄");
      const reply = turn(page, "지어낸 한 줄");
      await expect(reply.getByText("저장됐는지 확인하지 못했어요")).toBeVisible();

      await reply.getByRole("button", { name: "다시 시도" }).click();
      await expect(
        reply.getByText("앞서 처리된 결과예요. 같은 말을 다시 보내지 않아도 돼요."),
      ).toBeVisible();
      await expect(reply.getByText("기록 2건 저장됨")).toBeVisible();
      await expect(reply.getByText("저장됐는지 확인하지 못했어요")).toHaveCount(0);
      // 🚨 새 키로 다시 보내면 두 번 저장된다 — 이미 저장된 결과에 다시 보내는 길을 세우지 않는다.
      await expect(reply.getByRole("button", { name: "다시 시도" })).toHaveCount(0);
      await expect(reply.getByRole("button", { name: "고쳐 쓰기" })).toHaveCount(0);

      // 같은 키로 한 번 더 보냈고, SSE 는 처음 한 번뿐이다(지워진 채널은 404 다).
      const posts = writes(calls, "POST", /\/inputs$/);
      expect(posts).toHaveLength(2);
      expect(posts[1].idempotencyKey).toBe(posts[0].idempotencyKey);
      expect(calls.filter((c) => c.method === "GET" && c.path.startsWith("/runs/"))).toHaveLength(
        1,
      );
    });

    test("다시 보내지 않아도 서버에 그 턴이 오면 결과 미확인 카드가 저장된 결과로 바뀐다", async ({
      page,
    }) => {
      await sendFromHome(page, "지어낸 한 줄");
      await expect(
        turn(page, "지어낸 한 줄").getByText("저장됐는지 확인하지 못했어요"),
      ).toBeVisible();

      // 다음 한 줄이 끝나면 오늘 대화를 다시 받는다 — 그때 앞 줄의 턴이 서버에 있다.
      await page.getByRole("textbox", { name: "오늘 있었던 일" }).fill("다른 한 줄");
      await page.getByRole("button", { name: "이 이야기 남기기" }).click();

      const first = turn(page, "지어낸 한 줄");
      await expect(first.getByText("기록 2건 저장됨")).toBeVisible();
      await expect(first.getByText("저장됐는지 확인하지 못했어요")).toHaveCount(0);
      // 같은 말풍선이 두 번 서지 않는다.
      await expect(page.getByText("지어낸 한 줄", { exact: true })).toHaveCount(1);
    });
  });

  test.describe("replay_unknown — 채널도 대화 기록도 없다", () => {
    test.use({ scenario: "replay_unknown" });

    test("또 결과 미확인이고, 기록 탭에서 확인하라고 말한다", async ({ page }) => {
      await sendFromHome(page, "지어낸 한 줄");
      const reply = turn(page, "지어낸 한 줄");
      await expect(reply.getByText("저장됐는지 확인하지 못했어요")).toBeVisible();
      await expect(reply.getByRole("link", { name: "기록 확인하기" })).toHaveCount(0);

      await reply.getByRole("button", { name: "다시 시도" }).click();
      await expect(
        reply.getByText("다시 보내도 결과를 받지 못했어요.", { exact: false }),
      ).toBeVisible();
      await expect(reply.getByRole("link", { name: "기록 확인하기" })).toHaveAttribute(
        "href",
        `/child/${CHILD}/memories`,
      );
      // 저장 여부를 모른다 — "저장했어요" 도 "저장하지 않았어요" 도 말하지 않는다.
      await expect(reply.getByText("저장하지 않았어요", { exact: false })).toHaveCount(0);
      await expect(reply.getByText("이렇게 저장했어요")).toHaveCount(0);
    });

    test("고쳐 쓰기로 꺼내 그대로 다시 보내도 같은 안내가 서고, 같은 카드가 두 번 서지 않는다", async ({
      page,
    }) => {
      await sendFromHome(page, "지어낸 한 줄");
      const first = turn(page, "지어낸 한 줄");
      await expect(first.getByText("저장됐는지 확인하지 못했어요")).toBeVisible();

      await first.getByRole("button", { name: "고쳐 쓰기" }).click();
      const input = page.getByRole("textbox", { name: "오늘 있었던 일" });
      await expect(input).toHaveValue("지어낸 한 줄");
      await page.getByRole("button", { name: "이 이야기 남기기" }).click();

      await expect(
        page.getByText("다시 보내도 결과를 받지 못했어요.", { exact: false }),
      ).toBeVisible();
      await expect(page.getByRole("link", { name: "기록 확인하기" })).toHaveCount(1);
      // 같은 키 · 같은 run 이라 앞 줄은 뒤 줄이 대신한다.
      await expect(page.getByText("지어낸 한 줄", { exact: true })).toHaveCount(1);
      await expect(page.getByText("저장됐는지 확인하지 못했어요")).toHaveCount(1);
    });
  });
});

test.describe("건강 기록의 시각은 시각으로 그린다 (#485)", () => {
  test("서버가 보낸 ISO 시각을 그대로 찍지 않는다", async ({ page }) => {
    await sendFromHome(page, "계란볶음밥 먹었어");
    const reply = turn(page, "계란볶음밥 먹었어");
    await expect(reply.getByText("기록 2건 저장됨")).toBeVisible();
    // 날짜는 같은 줄의 "어제" 가 말한다 — 여기는 시각만 선다.
    await expect(reply.getByText("기침, 오후 9:00")).toBeVisible();
    await expect(reply.getByText(/T21:00/)).toHaveCount(0);
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

test.describe("기록 줄은 Agent 가 아니라 기록 종류로 선다 (#501)", () => {
  // 목은 `양치` 가 든 한 줄에 생활습관 · 교육 기록 2건을 저장한다 (`mocks/handlers/runs.ts` 의 GROWTH_WORDS).
  test("성장 Agent 의 두 기록이 '성장' 이 아니라 '생활습관' 과 '교육' 으로 선다", async ({
    page,
  }) => {
    await sendFromHome(page, "오늘 혼자 양치했어");
    const reply = turn(page, "오늘 혼자 양치했어");

    await expect(reply.getByText("기록 2건 저장됨")).toBeVisible();
    // 기록 줄 머리는 이름과 "· 언제" 가 한 덩어리라 머리글자로 찾는다.
    await expect(reply.getByText(/^생활습관/)).toBeVisible();
    await expect(reply.getByText(/^교육/)).toBeVisible();
    await expect(reply.getByText(/^성장/)).toHaveCount(0);
  });
});
