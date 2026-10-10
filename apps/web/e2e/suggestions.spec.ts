import { CHILD, expect, expectNoGate, gateMarks, recordApi, test, writes } from "./fixtures";

/**
 * 05 제안 후보 · 06 승인 시트 — 최상위 CLAUDE.md §2 기억 · 안전 · 실행.
 *
 * 개인화와 일반 추천을 가르는 것은 **라벨과 근거**지 색이 아니다 (apps/web/CLAUDE.md §4 · §5).
 * 승인 게이트는 ㉡ 알레르기 확인(채택 **전**)과 ㉠ 캘린더 쓰기(초안 제출) 두 곳이고,
 * 그 사이의 "이 N가지로 할게요"(채택)와 "일정으로 만들기"(초안 만들기)는 아무것도 확정하지 않는다.
 */

const SUGGESTIONS = `/child/${CHILD}/suggestions?agents=food,activity`;

test.describe("개인화 추천 (default)", () => {
  test("카드마다 사용한 기록이 붙고, 건수가 근거 줄 수와 같다", async ({ page }) => {
    const responded = page.waitForResponse(
      (r) => r.request().method() === "POST" && r.url().endsWith(`/children/${CHILD}/suggestions`),
    );
    await page.goto(SUGGESTIONS);
    const body = (await (await responded).json()) as {
      suggestions: { id: string; kind: string; content: string; evidence: unknown[] }[];
    };

    // 🚨 근거를 달고 나가는데 evidence 가 0행이면 버그다 (§2 기억 · 품질 지표 하드 기준 0건).
    expect(body.suggestions.length).toBeGreaterThan(0);
    for (const s of body.suggestions) {
      expect(s.kind, s.id).toBe("personalized");
      expect(s.evidence.length, `${s.id} 의 근거`).toBeGreaterThan(0);
    }

    for (const tab of ["식사 3가지", "놀이 3가지"]) {
      await page.getByRole("tab", { name: tab }).click();
      const panel = page.getByRole("tabpanel");
      const rows = panel.locator(":scope > ul > li");
      await expect(rows).toHaveCount(3);
      for (const row of await rows.all()) {
        const label = row.getByText(/^사용한 기록 \d+건$/);
        await expect(label).toBeVisible();
        const count = Number((await label.innerText()).match(/\d+/)![0]);
        expect(count).toBeGreaterThan(0);
        await expect(label.locator("xpath=following-sibling::ul[1]/li")).toHaveCount(count);
      }
    }

    // 일반 추천의 표시가 개인화 화면에 섞이지 않는다 — 둘은 한 화면에 같이 서지 않는다.
    await expect(page.getByText("또래 기준 일반 추천")).toHaveCount(0);
    await expect(page.getByText("질문은 한 개까지만 드려요.")).toHaveCount(0);
  });
});

test.describe("근거가 부족하면 일반 추천 (§2 기억)", () => {
  test.describe("scarcity — 기록 1건", () => {
    test.use({ scenario: "scarcity" });

    test("또래 기준이라고 말하고, 쌓인 기록 건수를 그대로 보여주고, 질문은 하나다", async ({
      page,
    }) => {
      await page.goto(SUGGESTIONS);
      const card = page.locator("li").filter({ hasText: "또래 기준 일반 추천" });
      await expect(card).toHaveCount(1);
      await expect(card).toContainText("기록 1건");
      await expect(card).toContainText("블록 쌓기처럼 손을 많이 쓰는 놀이를 15분쯤 해 보세요");

      await expect(
        page.getByRole("heading", { name: "다음엔 우리 아이 기준으로 골라 드릴게요" }),
      ).toBeVisible();
      await expect(
        page.getByText("이 주제로 쌓인 기록이 1건뿐이에요.", { exact: false }),
      ).toBeVisible();
      // 🚨 되물을 때는 최소 질문 1개 (복수 금지).
      await expect(page.getByText("요즘 실내와 야외 중 어디를 더 찾나요?")).toHaveCount(1);
      await expect(page.getByText("질문은 한 개까지만 드려요.")).toBeVisible();

      // 🚨 "우리 아이 맞춤" 인 척하지 않는다 — 개인화 화면의 부품이 하나도 없다.
      await expect(page.getByText(/^사용한 기록/)).toHaveCount(0);
      await expect(page.getByRole("checkbox")).toHaveCount(0);
      await expect(page.getByRole("tablist")).toHaveCount(0);
      await expect(page.getByText("이렇게 준비할게요")).toHaveCount(0);
      await expectNoGate(page);
    });

    test("일반 추천이 위, 되묻는 질문이 아래다", async ({ page }) => {
      await page.goto(SUGGESTIONS);
      const card = page.locator("li").filter({ hasText: "또래 기준 일반 추천" });
      const question = page.getByRole("heading", {
        name: "다음엔 우리 아이 기준으로 골라 드릴게요",
      });
      const cardBox = (await card.boundingBox())!;
      const questionBox = (await question.boundingBox())!;
      expect(cardBox.y).toBeLessThan(questionBox.y);
    });

    test("일반 추천과 개인화 추천을 색으로 가르지 않는다", async ({ page }) => {
      await page.goto(SUGGESTIONS);
      const card = page.locator("li").filter({ hasText: "또래 기준 일반 추천" });
      await expect(card).toBeVisible();
      // 도메인 칩 하나는 "어느 Agent 에서 왔나" 다. 그 밖에는 색 면이 없다 (apps/web §5).
      const tinted = await card.evaluate((root) => {
        const css = getComputedStyle(document.documentElement);
        const neutral = ["--color-canvas", "--color-surface", "--color-surface-muted"].map((n) =>
          css.getPropertyValue(n).trim().toLowerCase(),
        );
        const toHex = (rgb: string) =>
          "#" +
          (rgb.match(/\d+/g) ?? [])
            .slice(0, 3)
            .map((v) => Number(v).toString(16).padStart(2, "0"))
            .join("");
        const chip =
          root.querySelector("[data-domain-chip]") ?? root.firstElementChild?.firstElementChild;
        const found: string[] = [];
        for (const el of [root, ...root.querySelectorAll<HTMLElement>("*")]) {
          if (chip && (chip === el || chip.contains(el))) continue;
          const bg = getComputedStyle(el).backgroundColor;
          if (bg === "rgba(0, 0, 0, 0)" || bg === "transparent") continue;
          if (!neutral.includes(toHex(bg))) found.push(`${el.tagName} ${bg}`);
        }
        return found;
      });
      expect(tinted).toEqual([]);
    });
  });

  test.describe("empty — 기록 0건", () => {
    test.use({ scenario: "empty" });

    test("일반 추천마다 기록 0건이라고 말한다", async ({ page }) => {
      await page.goto(SUGGESTIONS);
      const cards = page.locator("li").filter({ hasText: "또래 기준 일반 추천" });
      await expect(cards).toHaveCount(2);
      for (const card of await cards.all()) await expect(card).toContainText("기록 0건");
      await expect(
        page.getByText("이 주제로 쌓인 기록이 아직 없어요.", { exact: false }),
      ).toBeVisible();
      await expect(page.getByText("질문은 한 개까지만 드려요.")).toHaveCount(1);
      await expect(page.getByText(/^사용한 기록/)).toHaveCount(0);
    });
  });
});

test.describe("알레르기 정보가 없으면 식사 제안을 만들지 않는다 (§2 안전)", () => {
  test.use({ scenario: "stale" });

  test("막힌 쪽의 제안은 한 줄도 서지 않는다", async ({ page }) => {
    await page.goto(SUGGESTIONS);
    await expect(page.getByText("식사 제안을 만들지 않았어요")).toBeVisible();
    await expect(
      page.getByText("알레르기 정보가 없어 식사 제안을 안전하게 걸러낼 수 없어요"),
    ).toBeVisible();
    await expect(page.getByRole("heading", { name: "오늘 저녁 뭐 차려 줄까요" })).toHaveCount(0);
    await expect(page.getByText("두부를 부쳐서 한 조각 곁들여 보세요")).toHaveCount(0);
    await expect(page.getByText("계란말이에 시금치를 조금 섞어 보세요")).toHaveCount(0);
    // 막힌 것은 실패가 아니다 — 남은 쪽을 실패 카드로 덮지 않는다.
    await expect(page.getByText(/쪽은 이번에 준비하지 못했어요/)).toHaveCount(0);
  });

  test("6개월 지난 근거는 그 사실을 글자로 말한다 (NF-08)", async ({ page }) => {
    await page.goto(SUGGESTIONS);
    await expect(page.getByText("6개월이 지난 기록이에요", { exact: false })).toBeVisible();
  });

  // caution 은 승인 게이트 2곳 전용이다. 한동안 guard 배너가 caution 이었다 (#242).
  test("guard 배너는 승인 게이트 색을 쓰지 않고, 알레르기를 적는 곳으로 보낸다", async ({
    page,
  }) => {
    await page.goto(SUGGESTIONS);
    await expect(page.getByText("식사 제안을 만들지 않았어요")).toBeVisible();
    await expectNoGate(page);
    // 서버 deeplink(settings/health-safety)를 주소로 쓰지 않는다 — 알레르기는 11 프로필에서 적는다.
    await expect(page.getByRole("link", { name: "알레르기 적으러 가기" })).toHaveAttribute(
      "href",
      `/child/${CHILD}/profile`,
    );
  });
});

test.describe("부분 결과 (§2 실행 NF-06)", () => {
  test.use({ scenario: "partial" });

  test("Agent 1개가 실패해도 성공한 쪽과 실패를 한 화면에 보여준다", async ({ page }) => {
    await page.goto(SUGGESTIONS);
    await expect(page.getByRole("heading", { name: "오늘 저녁 뭐 차려 줄까요" })).toBeVisible();
    await expect(page.getByText("사용한 기록", { exact: false })).toHaveCount(3);
    const failed = page
      .getByRole("status")
      .filter({ hasText: "놀이 쪽은 이번에 준비하지 못했어요." });
    await expect(failed).toBeVisible();
    await expect(failed.getByRole("button", { name: "다시 시도" })).toBeVisible();
  });
});

test.describe("승인 게이트 ㉡ — 알레르기 확인은 채택보다 먼저", () => {
  async function pickEggAndAdopt(page: import("@playwright/test").Page) {
    await page.goto(SUGGESTIONS);
    await page.getByText("계란말이에 시금치를 조금 섞어 보세요").click();
    await page.getByRole("button", { name: "이 1가지로 할게요" }).click();
    return page.getByRole("dialog", { name: "확인해 주세요" });
  }

  test("확인 시트가 먼저 뜨고, 답하기 전에는 아무것도 채택되지 않는다", async ({ page }) => {
    const calls = recordApi(page);
    const sheet = await pickEggAndAdopt(page);

    await expect(sheet).toBeVisible();
    await expect(sheet.getByText("처음 보는 재료가 있어요")).toBeVisible();
    await expect(sheet.getByRole("button", { name: "확인했어요" })).toBeDisabled();
    expect(writes(calls, "POST", /\/suggestions\/approve$/)).toEqual([]);

    // 🚨 되돌릴 수 없는 것 앞의 시트는 실수로 닫히지 않는다.
    await page.keyboard.press("Escape");
    await expect(sheet).toBeVisible();
  });

  test('"알레르기가 있어요" 는 Idempotency-Key 를 달고 저장되고, 그 제안은 채택에서 빠진다', async ({
    page,
  }) => {
    const calls = recordApi(page);
    const sheet = await pickEggAndAdopt(page);
    await sheet.getByRole("button", { name: "알레르기가 있어요" }).click();
    await expect(sheet.getByText("알레르기 기록에 추가했어요")).toBeVisible();

    const saved = writes(calls, "POST", /\/health-safety$/);
    expect(saved).toHaveLength(1);
    expect(saved[0].idempotencyKey).toBeTruthy();
    expect(saved[0].body).toMatchObject({ type: "allergy", label: "계란" });

    await sheet.getByRole("button", { name: "알레르기가 있는 건 빼고 고를게요" }).click();
    // s_1 만 골랐으므로 채택할 것이 남지 않는다.
    expect(writes(calls, "POST", /\/suggestions\/approve$/)).toEqual([]);
  });

  test('"괜찮았어요" 는 알레르기 기록을 만들지 않는다', async ({ page }) => {
    const calls = recordApi(page);
    const sheet = await pickEggAndAdopt(page);
    await sheet.getByRole("button", { name: "먹어봤어요 · 괜찮았어요" }).click();
    await sheet.getByRole("button", { name: "확인했어요" }).click();
    await expect(page.getByText("이렇게 하기로 했어요")).toBeVisible();
    expect(writes(calls, "POST", /\/health-safety$/)).toEqual([]);
  });

  test("놀이 제안이어도 먹을 것이 들어 있으면 묻는다 — 기준은 agent 가 아니다", async ({
    page,
  }) => {
    await page.goto(SUGGESTIONS);
    await page.getByRole("tab", { name: "놀이 3가지" }).click();
    await page.getByText("놀이터에 땅콩버터 쿠키를 간식으로 챙겨 가 보세요").click();
    await page.getByRole("button", { name: "이 1가지로 할게요" }).click();
    const sheet = page.getByRole("dialog", { name: "확인해 주세요" });
    await expect(sheet.getByText("땅콩 (알레르기 기록에 없음)")).toBeVisible();
  });
});

test.describe("승인 게이트 ㉡ — 화면이 모르는 사전검사", () => {
  test.use({ scenario: "precheck_unknown_code" });

  test("묻지 못하는 검사가 걸린 제안은 채택하지 않는다", async ({ page }) => {
    const calls = recordApi(page);
    await page.goto(SUGGESTIONS);
    await page.getByRole("tab", { name: "놀이 3가지" }).click();
    await page.getByText("주말에 실내 물놀이장은 어떨까요").click();
    await page.getByRole("button", { name: "이 1가지로 할게요" }).click();

    // 🚨 예전에는 시트가 아무것도 묻지 않은 채 "확인했어요" 로 그대로 채택했다.
    const sheet = page.getByRole("dialog", { name: "확인해 주세요" });
    await expect(sheet.getByText("여기서 확인할 수 없는 항목이 있어요")).toBeVisible();
    await expect(sheet.getByRole("button", { name: "확인했어요" })).toHaveCount(0);
    await sheet.getByRole("button", { name: "확인하지 못한 건 빼고 고를게요" }).click();
    expect(writes(calls, "POST", /\/suggestions\/approve$/)).toEqual([]);
    expect(writes(calls, "POST", /\/health-safety$/)).toEqual([]);
  });
});

test.describe("채택과 초안 만들기는 게이트가 아니다 · 승인 게이트 ㉠ 은 초안 제출", () => {
  test("놀이 제안 채택 → 일정으로 만들기 → 날짜를 고르고 넣을 때만 캘린더에 쓴다", async ({
    page,
  }) => {
    const calls = recordApi(page);
    await page.goto(SUGGESTIONS);
    await page.getByRole("tab", { name: "놀이 3가지" }).click();
    await page.getByText("주말에 실내 물놀이장은 어떨까요").click();

    // 채택 — 이 제안엔 알레르기 항목(`allergens`)이 없어서 시트 없이 바로 간다.
    // 게이트가 아니라 표식도 키도 없다.
    await expectNoGate(page);
    await page.getByRole("button", { name: "이 1가지로 할게요" }).click();
    await expect(page.getByText("이렇게 하기로 했어요")).toBeVisible();
    const approve = writes(calls, "POST", /\/suggestions\/approve$/);
    expect(approve).toHaveLength(1);
    expect(approve[0].idempotencyKey).toBeUndefined();

    // 🚨 채택 뒤에는 고르기가 잠긴다 — 서버가 받은 것과 화면이 달라지면 안 된다.
    const checkbox = page.getByRole("checkbox", { name: /주말에 실내 물놀이장은 어떨까요/ });
    await page.getByText("주말에 실내 물놀이장은 어떨까요").click();
    await expect(checkbox).toBeChecked();

    // 초안 만들기 — 아무것도 쓰지 않는다 (#121).
    await page.getByRole("button", { name: "일정으로 만들기" }).click();
    const sheet = page.getByRole("dialog", { name: "캘린더에 넣을까요" });
    await expect(sheet).toBeVisible();
    expect(writes(calls, "POST", /\/events$/)).toEqual([]);
    expect(
      writes(calls, "POST", /\/suggestions\/event-drafts$/)[0]?.idempotencyKey,
    ).toBeUndefined();

    // 🚨 날짜가 없으면 넣을 수 없다 — 자정으로 채우지 않는다.
    const submit = sheet.getByRole("button", { name: "확인했어요, 캘린더에 넣을게요" });
    await expect(submit).toBeDisabled();
    await expect(sheet.getByText("날짜를 골라주셔야 넣을 수 있어요.")).toBeVisible();
    expect(await gateMarks(page, "dialog[open]")).toContain(
      "approve 버튼: 확인했어요, 캘린더에 넣을게요",
    );

    await page.keyboard.press("Escape");
    await expect(sheet).toBeVisible();

    await sheet.getByRole("button", { name: /언제/ }).click();
    // 날짜 버튼의 이름은 "2026년 10월 7일 수요일" 꼴이고 오늘은 "오늘, " 이 앞에 붙는다.
    await page.getByRole("button", { name: /^오늘, / }).click();
    await page.getByRole("button", { name: "고르기" }).click();
    await expect(submit).toBeEnabled();
    expect(writes(calls, "POST", /\/events$/)).toEqual([]);

    await submit.click();
    await expect(sheet.getByText("넣었어요", { exact: true })).toBeVisible();
    const submitted = writes(calls, "POST", /\/children\/c1\/events$/);
    expect(submitted).toHaveLength(1);
    expect(submitted[0].idempotencyKey).toBeTruthy();
    expect(submitted[0].body).toMatchObject({ suggestion_ids: ["s_4"] });
  });
});
