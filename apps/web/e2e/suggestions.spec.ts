import {
  CHILD,
  asOtherCaregiver,
  expect,
  expectNoGate,
  gateMarks,
  recordApi,
  test,
  writes,
} from "./fixtures";

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

    // 기본 시나리오는 개인화만 온다 — 일반 추천이 없으면 "또래 기준" 표시도 되묻는 질문도 서지 않는다.
    // (같은 Agent 에 둘이 섞이는 경우는 아래 `suggest_mixed` 가 건다.)
    await expect(page.getByText("또래 기준 일반 추천")).toHaveCount(0);
    await expect(page.getByText("질문은 한 개까지만 드려요.")).toHaveCount(0);
  });

  test("이유는 서버 문장 그대로 한 줄이고, 비어 오면 라벨도 세우지 않는다 (#475)", async ({
    page,
  }) => {
    const responded = page.waitForResponse(
      (r) => r.request().method() === "POST" && r.url().endsWith(`/children/${CHILD}/suggestions`),
    );
    await page.goto(SUGGESTIONS);
    const body = (await (await responded).json()) as {
      suggestions: { agent: string; content: string; reason: string | null }[];
    };
    // 빈 이유가 한 줄도 없으면 이 테스트는 "라벨만 선 줄" 을 못 잡는다
    expect(body.suggestions.some((s) => s.reason === null)).toBe(true);

    for (const [tab, agent] of [
      ["식사 3가지", "food"],
      ["놀이 3가지", "activity"],
    ] as const) {
      await page.getByRole("tab", { name: tab }).click();
      const panel = page.getByRole("tabpanel");
      for (const s of body.suggestions.filter((x) => x.agent === agent)) {
        const row = panel.locator(":scope > ul > li").filter({ hasText: s.content });
        // 행을 못 찾으면 아래 "라벨 0건" 이 그냥 통과한다 — 행이 있다는 전제를 먼저 건다
        await expect(row).toHaveCount(1);
        const label = row.getByText("추천 이유", { exact: true });
        if (s.reason) {
          await expect(label).toBeVisible();
          await expect(row.getByText(s.reason, { exact: true })).toBeVisible();
        } else {
          await expect(label).toHaveCount(0);
        }
      }
      // 서버가 한 문장으로 보내는 것을 화면이 두 칸으로 나누지 않는다 (Agent 공통규약 C-2)
      await expect(panel.getByText(/^왜 (이걸|지금)$/)).toHaveCount(0);
    }
  });
});

test.describe("홈 버튼에서 들어오면 (#286)", () => {
  const suggestionsBody = (calls: ReturnType<typeof recordApi>) =>
    writes(calls, "POST", new RegExp(`^/children/${CHILD}/suggestions$`)).map((c) => c.body);

  test("누른 버튼의 키만 추천 요청에 싣고, 문장은 보내지 않는다", async ({ page }) => {
    const calls = recordApi(page);
    await page.goto(`/child/${CHILD}/home`);
    await page.getByRole("button", { name: /저녁 추천해드릴까요/ }).click();

    await expect(page).toHaveURL(/\/suggestions\?agents=food&prompt=dinner$/);
    await expect(page.getByText("사용한 기록", { exact: false }).first()).toBeVisible();
    // 🚨 문장이 실리면 Supervisor 사전검사를 안 거친 문장으로 Agent 가 돈다 (#249).
    expect(suggestionsBody(calls)).toEqual([{ agents: ["food"], prompt_key: "dinner" }]);
  });

  test("주소의 모르는 키는 버리고 키 없이 요청한다", async ({ page }) => {
    const calls = recordApi(page);
    await page.goto(`/child/${CHILD}/suggestions?agents=food&prompt=midnight_snack`);
    await expect(page.getByText("사용한 기록", { exact: false }).first()).toBeVisible();
    expect(suggestionsBody(calls)).toEqual([{ agents: ["food"] }]);
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

      // 🚨 "우리 아이 맞춤" 인 척하지 않는다 — 아이 기록 근거도, 개인화 묶음 탭도 없다.
      //    고르는 칸은 있다 (#509) — 고르는 것은 개인화의 부품이 아니다.
      await expect(page.getByText(/^사용한 기록/)).toHaveCount(0);
      await expect(page.getByRole("tablist")).toHaveCount(0);
      await expectNoGate(page);
    });

    test("일반 추천도 골라서 채택하고 일정으로 만든다 (#509)", async ({ page }) => {
      const calls = recordApi(page);
      await page.goto(SUGGESTIONS);
      const card = page.locator("li").filter({ hasText: "또래 기준 일반 추천" });
      const checkbox = card.getByRole("checkbox", { name: /블록 쌓기처럼/ });

      // 🚨 다음 행동은 고를 것보다 아래다.
      const summary = page.getByText("이렇게 준비할게요");
      expect((await card.boundingBox())!.y).toBeLessThan((await summary.boundingBox())!.y);

      await card.getByText("블록 쌓기처럼 손을 많이 쓰는 놀이를 15분쯤 해 보세요").click();
      await expect(checkbox).toBeChecked();
      // 🚨 골라도 점선은 남는다 — 고른다고 우리 아이 기준 추천이 되는 것이 아니다.
      expect(await card.evaluate((el) => getComputedStyle(el).borderTopStyle)).toBe("dashed");

      // 채택은 게이트가 아니다 — 표식도 키도 없다.
      await expectNoGate(page);
      await page.getByRole("button", { name: "이 1가지로 할게요" }).click();
      await expect(page.getByText("이렇게 하기로 했어요")).toBeVisible();
      const approve = writes(calls, "POST", /\/suggestions\/approve$/);
      expect(approve).toHaveLength(1);
      expect(approve[0].body).toMatchObject({ suggestion_ids: ["g_1"] });
      expect(approve[0].idempotencyKey).toBeUndefined();

      // 초안 만들기는 아무것도 쓰지 않는다. 캘린더에 쓰는 것은 승인 시트의 제출 하나다 (게이트 ㉠).
      await page.getByRole("button", { name: "일정으로 만들기" }).click();
      await expect(page.getByRole("dialog", { name: "캘린더에 넣을까요" })).toBeVisible();
      const drafts = writes(calls, "POST", /\/suggestions\/event-drafts$/);
      expect(drafts).toHaveLength(1);
      expect(drafts[0].body).toMatchObject({ suggestion_ids: ["g_1"] });
      expect(writes(calls, "POST", /\/events$/)).toEqual([]);
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

    test("일반 추천도 알레르기 확인이 채택보다 먼저다 (#509 · 게이트 ㉡)", async ({ page }) => {
      const calls = recordApi(page);
      await page.goto(SUGGESTIONS);
      await page.getByText("국물 없이 집어 먹는 반찬을 한 가지 곁들여 보세요").click();
      await page.getByRole("button", { name: "이 1가지로 할게요" }).click();

      const sheet = page.getByRole("dialog");
      await expect(sheet.getByText("처음 보는 재료가 있어요")).toBeVisible();
      await expect(sheet.getByText("참깨")).toBeVisible();
      expect(writes(calls, "POST", /\/suggestions\/approve$/)).toEqual([]);
    });
  });
});

test.describe("개인화와 일반이 같은 Agent 에 섞여 오면 (#512 리뷰)", () => {
  test.use({ scenario: "suggest_mixed" });

  test("묶음 안내는 개인화와 일반을 합쳐 세고 한 번만 선다", async ({ page }) => {
    await page.goto(SUGGESTIONS);
    const notice = page.getByText(/일정 하나로 묶여요/);

    await page.getByText("계란말이에 시금치를 조금 섞어 보세요").click();
    // 하나만 고르면 묶일 것이 없다
    await expect(notice).toHaveCount(0);

    // 🚨 서버는 kind 가 아니라 Agent 로 묶는다 — 개인화 1 + 일반 1 도 초안 한 장이다
    await page.getByText("국물 없이 집어 먹는 반찬을 한 가지 곁들여 보세요").click();
    await expect(notice).toHaveCount(1);
    await expect(notice).toHaveText("식사에서 고른 2가지는 일정 하나로 묶여요.");

    await page.getByText("두부를 부쳐서 한 조각 곁들여 보세요").click();
    await expect(notice).toHaveText("식사에서 고른 3가지는 일정 하나로 묶여요.");
  });

  test("묶음 건수 줄은 개인화만 셌다는 것을 글자로 말한다", async ({ page }) => {
    await page.goto(SUGGESTIONS);
    // 아래 일반 카드까지 식사 후보는 넷이다 — "3가지 중에서" 만 쓰면 숫자가 어긋난다
    await expect(page.getByText("우리 아이 기준 식사 3가지 중에서")).toBeVisible();
    await expect(page.getByText("식사 · 3가지 중에서")).toHaveCount(0);
  });

  test("질문 카드는 위의 개인화까지 또래 기준이라고 부르지 않는다", async ({ page }) => {
    await page.goto(SUGGESTIONS);
    // 둘 다 선다 — 근거가 붙은 개인화와 라벨이 붙은 일반
    await expect(page.getByText(/^사용한 기록/).first()).toBeVisible();
    await expect(page.locator("li").filter({ hasText: "또래 기준 일반 추천" })).toHaveCount(1);

    await expect(page.getByText("위 추천은 또래 기준이에요", { exact: false })).toHaveCount(0);
    // 🚨 제목도 "다음엔 우리 아이 기준으로" 가 아니다 — 위에 우리 아이 기준 추천이 이미 서 있다
    await expect(
      page.getByRole("heading", { name: "다음엔 우리 아이 기준으로 골라 드릴게요" }),
    ).toHaveCount(0);
    await expect(
      page.getByRole("heading", { name: "기록이 더 쌓이면 모두 우리 아이 기준으로 골라 드릴게요" }),
    ).toBeVisible();
    await expect(
      page.getByText("또래 기준이라고 적힌 추천은 우리 아이 기록 대신 또래 기준으로 골랐어요", {
        exact: false,
      }),
    ).toBeVisible();
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

  test("다른 보호자가 먼저 등록한 재료면 기록된 것으로 서고, 다시 시도가 없다 (#374)", async ({
    page,
  }) => {
    const sheet = await pickEggAndAdopt(page);
    expect(
      await asOtherCaregiver(page, {
        method: "POST",
        path: `/children/${CHILD}/health-safety`,
        body: { type: "allergy", label: "계란" },
        idempotent: true,
      }),
    ).toBe(201);

    // 🚨 409 already_exists 는 "이미 기록에 있다" 다 — 다음 제안에서도 걸러진다.
    await sheet.getByRole("button", { name: "알레르기가 있어요" }).click();
    await expect(sheet.getByText("알레르기 기록에 추가했어요")).toBeVisible();
    await expect(
      sheet.getByText("알레르기 기록에 저장하지 못했어요", { exact: false }),
    ).toHaveCount(0);
    await expect(sheet.getByRole("button", { name: "다시 시도" })).toHaveCount(0);
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

test.describe("0개 · 이어서 할 수 없음은 실패가 아니다 (#249)", () => {
  test.describe("개수 안내", () => {
    test.use({ scenario: "suggest_counts" });

    test("0개로 끝난 Agent 는 안내만 서고 다시 시도가 없다", async ({ page }) => {
      await page.goto(SUGGESTIONS);

      // 서버 문구를 그대로 그린다 — 놀이는 0개, 식사는 2개
      await expect(page.getByText("조건에 맞는 추천이 없어요.")).toBeVisible();
      await expect(page.getByText("조건에 맞는 추천을 2개 준비했어요.")).toBeVisible();
      // 🚨 실패는 "묶음이 없는 Agent" 다 — 0개를 실패 카드로 그리면 눌러도 같은 0개만 다시 온다.
      await expect(page.getByText(/쪽은 이번에 준비하지 못했어요/)).toHaveCount(0);
      await expect(page.getByRole("button", { name: "다시 시도" })).toHaveCount(0);
    });
  });

  test.describe("이어서 할 수 없음", () => {
    test.use({ scenario: "cannot_resume" });

    test("안내만 서고 다시 시도가 없고, 이어서 할 수 있는 쪽은 그대로 보인다", async ({ page }) => {
      await page.goto(`${SUGGESTIONS}&run=r1`);

      await expect(
        page.getByText("이전 요청을 이어서 준비할 수 없어요. 홈에서 다시 골라 주세요."),
      ).toBeVisible();
      await expect(page.getByRole("button", { name: "다시 시도" })).toHaveCount(0);
      // 성공과 안내를 한 화면에 섞는다 (NF-06) — 식사 후보는 그대로 선다.
      // 서버가 `prompt` 를 아직 안 주므로(null) 머리줄은 도메인 라벨이다
      await expect(page.getByText("식사 · 3가지 중에서")).toBeVisible();
    });
  });

  test.describe("일반 추천의 근거", () => {
    test.use({ scenario: "scarcity" });

    test("문서 출처를 적고, 아이 기록처럼 세지 않는다", async ({ page }) => {
      await page.goto(SUGGESTIONS);

      const card = page.locator("li").filter({ hasText: "또래 기준 일반 추천" });
      await expect(card).toContainText("참고 · 블록 쌓기 (교육부, 표준보육과정)");
      // 🚨 문서는 아이 기록이 아니다 — "사용한 기록 N건" 으로 세지 않는다
      await expect(page.getByText(/^사용한 기록/)).toHaveCount(0);
      // 답하는 길(#246) 전에는 선택지가 비어 온다 — 문장으로만 서고 칩이 없다
      await expect(page.getByText("요즘 실내와 야외 중 어디를 더 찾나요?")).toBeVisible();
      await expect(page.getByRole("button", { name: "실내" })).toHaveCount(0);
    });
  });

  test.describe("식품 아닌 알레르기 확인 문구", () => {
    test.use({ scenario: "suggest_cautions" });

    test("놀이 묶음에 확인 문구가 선다", async ({ page }) => {
      await page.goto(SUGGESTIONS);
      await expect(
        page.getByText("등록된 알레르기(꽃가루)가 있어요. 장소랑 재료를 한 번 확인해 주세요."),
      ).toBeVisible();
    });
  });

  test.describe("하루 한도", () => {
    test.use({ scenario: "daily_limit" });

    test("🚨 다시 시도를 세우지 않는다 — 눌러도 같은 답이다", async ({ page }) => {
      await page.goto(SUGGESTIONS);
      await expect(
        page.getByText("오늘은 더 준비할 수 없어요. 내일 다시 골라 주세요."),
      ).toBeVisible();
      await expect(page.getByRole("button", { name: "다시 시도" })).toHaveCount(0);
    });
  });
});
