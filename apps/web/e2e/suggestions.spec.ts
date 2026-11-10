import type { Locator, Page } from "@playwright/test";

import {
  CHILD,
  asOtherCaregiver,
  expect,
  expectNoGate,
  gateMarks,
  recordApi,
  test,
  switchScenario,
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

    // 기본 시나리오는 개인화만 온다 — 일반 추천이 없으면 "일반 추천" 표시도 되묻는 질문도 서지 않는다.
    // (같은 Agent 에 둘이 섞이는 경우는 아래 `suggest_mixed` 가 건다.)
    await expect(page.getByText("일반 추천", { exact: true })).toHaveCount(0);
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

test.describe("들어온 곳으로 돌아가기 (#683)", () => {
  test("홈 버튼에서 왔으면 위 화살표가 홈으로 간다", async ({ page }) => {
    await page.goto(`/child/${CHILD}/home`);
    await page.getByRole("button", { name: /저녁 추천해드릴까요/ }).click();
    await expect(page.getByText("사용한 기록", { exact: false }).first()).toBeVisible();

    await expect(page.getByRole("link", { name: "대화로 돌아가기" })).toHaveCount(0);
    await page.getByRole("link", { name: "홈으로 돌아가기" }).click();
    await expect(page).toHaveURL(new RegExp(`/child/${CHILD}/home$`));
  });

  test("대화에서 왔으면 위 화살표가 대화로 가고, 스크롤해도 화면 위에 남는다", async ({ page }) => {
    // 04 "제안 보기" 는 run 을 싣고, "이것도 도와드릴까요?" 는 영역만 싣는다 — 둘 다 대화다
    for (const path of [`${SUGGESTIONS}&run=r1`, SUGGESTIONS]) {
      await page.goto(path);
      await expect(page.getByText("사용한 기록", { exact: false }).first()).toBeVisible();
      const back = page.getByRole("link", { name: "대화로 돌아가기" });
      await expect(back).toHaveAttribute("href", `/child/${CHILD}/chat`);
      await expect(page.getByRole("link", { name: "홈으로 돌아가기" })).toHaveCount(0);

      // 🚨 나가는 길이 본문에 있으면 후보 셋을 지나 한참 내려가야 한다 — 맨 아래에서도 보여야 한다
      await page.getByRole("link", { name: "홈으로", exact: true }).scrollIntoViewIfNeeded();
      await expect(back).toBeInViewport();
    }

    await page.getByRole("link", { name: "대화로 돌아가기" }).click();
    await expect(page).toHaveURL(new RegExp(`/child/${CHILD}/chat$`));
  });

  test("맨 아래 홈으로는 버튼이 아니라 링크다", async ({ page }) => {
    await page.goto(SUGGESTIONS);
    await expect(page.getByText("사용한 기록", { exact: false }).first()).toBeVisible();
    await expect(page.getByRole("button", { name: "홈으로", exact: true })).toHaveCount(0);
    await expect(page.getByRole("link", { name: "홈으로", exact: true })).toHaveAttribute(
      "href",
      `/child/${CHILD}/home`,
    );
  });
});

test.describe("근거가 부족하면 일반 추천 (§2 기억)", () => {
  test.describe("scarcity — 기록 1건", () => {
    test.use({ scenario: "scarcity" });

    test("일반 추천이라고 말하고, 쌓인 기록 건수를 그대로 보여 주고, 질문은 하나다", async ({
      page,
    }) => {
      await page.goto(SUGGESTIONS);
      const card = page.locator("li").filter({ has: page.getByText("일반 추천", { exact: true }) });
      await expect(card).toHaveCount(1);
      await expect(card).toContainText("기록 1건");
      await expect(card).toContainText("블록 쌓기처럼 손을 많이 쓰는 놀이를 15분쯤 해 보세요");
      // 🚨 라벨은 "또래 기준" 을 말하지 않는다 — 기준은 서버 문구(`basis`)가 말한다 (#597)
      await expect(card).toContainText("36개월 또래가 자주 찾는 놀이예요");
      await expect(page.getByText("또래 기준", { exact: false })).toHaveCount(0);

      await expect(
        page.getByRole("heading", { name: "이번에는 일반 추천으로 골랐어요" }),
      ).toBeVisible();
      await expect(page.getByText("놀이 기록은 1건 있어요.", { exact: false })).toBeVisible();
      await expect(
        page.getByText("이번에는 아이 기록을 쓰지 않고, 추천마다 아래 적힌 기준으로 골랐어요.", {
          exact: false,
        }),
      ).toBeVisible();
      // 🚨 누적 건수를 "적다" 고 단정하거나 쌓이면 달라진다고 약속하지 않는다 (#662)
      await expect(page.getByText("뿐이에요", { exact: false })).toHaveCount(0);
      await expect(page.getByText("더 쌓이면", { exact: false })).toHaveCount(0);
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
      const card = page.locator("li").filter({ has: page.getByText("일반 추천", { exact: true }) });
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
      const card = page.locator("li").filter({ has: page.getByText("일반 추천", { exact: true }) });
      const question = page.getByRole("heading", {
        name: "이번에는 일반 추천으로 골랐어요",
      });
      const cardBox = (await card.boundingBox())!;
      const questionBox = (await question.boundingBox())!;
      expect(cardBox.y).toBeLessThan(questionBox.y);
    });

    test("일반 추천과 개인화 추천을 색으로 가르지 않는다", async ({ page }) => {
      await page.goto(SUGGESTIONS);
      const card = page.locator("li").filter({ has: page.getByText("일반 추천", { exact: true }) });
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
      const cards = page
        .locator("li")
        .filter({ has: page.getByText("일반 추천", { exact: true }) });
      await expect(cards).toHaveCount(2);
      for (const card of await cards.all()) await expect(card).toContainText("기록 0건");
      await expect(
        page.getByText("놀이와 식사 기록은 아직 없어요.", { exact: false }),
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
      await expect(sheet.getByText("처음 보는 식품이 있어요")).toBeVisible();
      await expect(sheet.getByText("참깨")).toBeVisible();
      expect(writes(calls, "POST", /\/suggestions\/approve$/)).toEqual([]);
    });
  });

  test.describe("Agent 둘의 일반 추천 — 기록 수를 합치지 않는다 (#662)", () => {
    test.use({ scenario: "suggest_multi_general" });

    test("식사와 놀이 기록을 Agent별로 보여 주고 합계 30건은 쓰지 않는다", async ({ page }) => {
      await page.goto(SUGGESTIONS);

      const food = page
        .locator("li")
        .filter({ has: page.getByText("국물 없이 집어 먹는 반찬을 한 가지 곁들여 보세요") });
      const activity = page
        .locator("li")
        .filter({ has: page.getByText("블록 쌓기처럼 손을 많이 쓰는 놀이를 15분쯤 해 보세요") });
      await expect(food.getByText("일반 추천", { exact: true })).toBeVisible();
      await expect(activity.getByText("일반 추천", { exact: true })).toBeVisible();
      await expect(food).toContainText("기록 22건");
      await expect(activity).toContainText("기록 8건");

      await expect(
        page.getByRole("heading", { name: "이번에는 일반 추천으로 골랐어요" }),
      ).toBeVisible();
      await expect(page.getByText("영역별 기록은 식사 22건, 놀이 8건이에요.")).toBeVisible();
      for (const banned of ["30건", "이 주제로", "또래 기준", "다음부터 달라져요"]) {
        await expect(page.getByText(banned, { exact: false })).toHaveCount(0);
      }
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

  test("개인화 후보와 일반 추천의 추천 문장은 같은 글씨다 (#683)", async ({ page }) => {
    await page.goto(SUGGESTIONS);
    const font = (text: string) =>
      page.getByText(text, { exact: true }).evaluate((el) => {
        const style = getComputedStyle(el);
        return { size: style.fontSize, weight: style.fontWeight };
      });

    const personal = await font("계란말이에 시금치를 조금 섞어 보세요");
    const general = await font("국물 없이 집어 먹는 반찬을 한 가지 곁들여 보세요");
    // 🚨 개인화만 크고 굵으면 더 중요한 추천처럼 읽힌다 — 가르는 것은 라벨 · 점선 · 근거다
    expect(personal).toEqual(general);

    // 묶음 질문은 후보보다 크고, 화면 제목보다는 작다 — 층이 셋으로 갈린다.
    // 🚨 좁은 폰(360)에서도 본다 — 화면 제목이 380 미만에서 한 단계 내려가서, 질문도 같이 내려야 한다.
    const px = (value: string) => Number.parseFloat(value);
    const size = (heading: string) =>
      page
        .getByRole("heading", { name: heading, exact: true })
        .evaluate((el) => getComputedStyle(el).fontSize);
    for (const width of [390, 360]) {
      await page.setViewportSize({ width, height: 844 });
      const candidate = px((await font("계란말이에 시금치를 조금 섞어 보세요")).size);
      const prompt = px(await size("오늘 저녁 뭐 차려 줄까요"));
      expect(prompt, `${width} 폭`).toBeGreaterThan(candidate);
      expect(prompt, `${width} 폭`).toBeLessThan(px(await size("추천 후보")));
    }
  });

  test("묶음 건수 줄은 개인화만 셌다는 것을 글자로 말한다", async ({ page }) => {
    await page.goto(SUGGESTIONS);
    // 아래 일반 카드까지 식사 후보는 넷이다 — "3가지 중에서" 만 쓰면 숫자가 어긋난다
    await expect(page.getByText("우리 아이 기준 식사 3가지 중에서")).toBeVisible();
    await expect(page.getByText("식사 · 3가지 중에서")).toHaveCount(0);
  });

  test("질문 카드는 위의 개인화까지 일반 추천이라고 부르지 않는다", async ({ page }) => {
    await page.goto(SUGGESTIONS);
    // 둘 다 선다 — 근거가 붙은 개인화와 라벨이 붙은 일반
    await expect(page.getByText(/^사용한 기록/).first()).toBeVisible();
    await expect(
      page.locator("li").filter({ has: page.getByText("일반 추천", { exact: true }) }),
    ).toHaveCount(1);

    await expect(page.getByText("위 추천은", { exact: false })).toHaveCount(0);
    // 🚨 제목이 위에 선 개인화까지 일반이라고 부르지 않는다 — "일부는" 이다
    await expect(
      page.getByRole("heading", { name: "이번에는 일반 추천으로 골랐어요" }),
    ).toHaveCount(0);
    await expect(
      page.getByRole("heading", { name: "일부는 일반 추천으로 골랐어요" }),
    ).toBeVisible();
    await expect(
      page.getByText("일반 추천이라고 적힌 것은 아이 기록 없이 골랐어요", {
        exact: false,
      }),
    ).toBeVisible();
    // 🚨 누적 건수를 "적다" 고 단정하거나 쌓이면 달라진다고 약속하지 않는다 (#662)
    await expect(page.getByText("뿐이에요", { exact: false })).toHaveCount(0);
    await expect(page.getByText("더 쌓이면", { exact: false })).toHaveCount(0);
  });
});

test.describe("장소가 붙은 추천의 확인 문구 (#516)", () => {
  test.use({ scenario: "suggest_place" });

  const PERSONAL_NOTICE = "공공데이터 기준 2026년 6월이에요. 가기 전에 운영 여부를 확인해 주세요.";
  const GENERAL_NOTICE = "가기 전에 운영 여부를 확인해 주세요.";

  test("개인화 줄은 추천 문장 아래 한 줄로 세우고, 사용한 기록에는 넣지 않는다", async ({
    page,
  }) => {
    await page.goto(SUGGESTIONS);
    await page.getByRole("tab", { name: "놀이 3가지" }).click();
    const panel = page.getByRole("tabpanel");
    const row = panel.locator(":scope > ul > li").filter({ hasText: "햇살어린이공원에서" });
    await expect(row).toHaveCount(1);

    const notice = row.getByText(PERSONAL_NOTICE, { exact: true });
    await expect(notice).toBeVisible();
    // 🚨 고르는 칸의 이름은 제안 문장뿐이다 — 문구가 라벨에 섞이면 체크박스 라벨이 길어진다
    await expect(
      row.getByRole("checkbox", { name: "햇살어린이공원에서 모래성 쌓기", exact: true }),
    ).toBeVisible();
    // 제안 문장 바로 아래, 이유보다 위다
    const reasonLabel = row.getByText("추천 이유", { exact: true });
    expect((await notice.boundingBox())!.y).toBeLessThan((await reasonLabel.boundingBox())!.y);

    // 🚨 장소는 근거가 아니다 — 근거 목록 안에 없고, 건수는 근거 줄 수 그대로다
    const label = row.getByText(/^사용한 기록 \d+건$/);
    const list = label.locator("xpath=following-sibling::ul[1]");
    const count = Number((await label.innerText()).match(/\d+/)![0]);
    await expect(list.locator("li")).toHaveCount(count);
    await expect(list.getByText(/운영 여부/)).toHaveCount(0);

    // 장소가 없는 줄에는 서지 않는다
    await expect(panel.getByText(/운영 여부를 확인해 주세요/)).toHaveCount(1);
  });

  test("일반 카드는 추천 문장 아래, 기준 문구보다 위에 세운다", async ({ page }) => {
    await page.goto(SUGGESTIONS);
    const card = page.locator("li").filter({ hasText: "별빛작은도서관에서" });
    await expect(card).toHaveCount(1);
    await expect(card.getByText("일반 추천", { exact: true })).toBeVisible();
    // 🚨 기록 건수는 그 Agent 도메인 수 그대로다 — 장소 문구가 붙어도 늘지 않는다
    await expect(card).toContainText("기록 3건");

    const notice = card.getByText(GENERAL_NOTICE, { exact: true });
    await expect(notice).toBeVisible();
    const basis = card.getByText("36개월 또래가 자주 찾는 놀이예요", { exact: true });
    expect((await notice.boundingBox())!.y).toBeLessThan((await basis.boundingBox())!.y);
  });
});

test.describe("행사가 붙은 추천의 기간 (#681)", () => {
  test.use({ scenario: "suggest_event" });

  const NOTICE =
    "한국관광공사 행사 정보예요. 일정과 관람 연령이 바뀔 수 있으니 가기 전에 확인해 주세요.";

  test("개인화 줄은 확인 문구 바로 위에 기간을 세우고, 사용한 기록에는 넣지 않는다", async ({
    page,
  }) => {
    await page.goto(SUGGESTIONS);
    await page.getByRole("tab", { name: "놀이 3가지" }).click();
    const panel = page.getByRole("tabpanel");
    const row = panel.locator(":scope > ul > li").filter({ hasText: "들꽃마을 가을 축제에서" });
    await expect(row).toHaveCount(1);

    const period = row.getByText("2026년 10월 30일부터 11월 1일까지 열려요", { exact: true });
    await expect(period).toBeVisible();
    // 🚨 고르는 칸의 이름은 제안 문장뿐이다
    await expect(
      row.getByRole("checkbox", { name: "들꽃마을 가을 축제에서 꽃 이름 맞히기", exact: true }),
    ).toBeVisible();
    // 제안 문장 아래, 확인 문구 바로 위다
    const label = row.getByText("들꽃마을 가을 축제에서 꽃 이름 맞히기", { exact: true });
    const notice = row.getByText(NOTICE, { exact: true });
    const y = async (l: typeof period) => (await l.boundingBox())!.y;
    expect(await y(label)).toBeLessThan(await y(period));
    expect(await y(period)).toBeLessThan(await y(notice));

    // 🚨 기간은 근거가 아니다 — 근거 목록 안에 없고, 건수는 근거 줄 수 그대로다
    const used = row.getByText(/^사용한 기록 \d+건$/);
    const list = used.locator("xpath=following-sibling::ul[1]");
    const count = Number((await used.innerText()).match(/\d+/)![0]);
    await expect(list.locator("li")).toHaveCount(count);
    await expect(list.getByText(/열려요/)).toHaveCount(0);

    // 행사가 없는 줄에는 서지 않는다
    await expect(panel.getByText(/열려요$/)).toHaveCount(1);
  });

  test("일반 카드는 하루짜리 행사를 날짜 하나로 적고, 기준 문구보다 위에 세운다", async ({
    page,
  }) => {
    await page.goto(SUGGESTIONS);
    const card = page.locator("li").filter({ hasText: "솔숲 어린이 인형극 한마당에서" });
    await expect(card).toHaveCount(1);
    await expect(card.getByText("일반 추천", { exact: true })).toBeVisible();
    // 🚨 기록 건수는 그 Agent 도메인 수 그대로다
    await expect(card).toContainText("기록 3건");

    const period = card.getByText("2026년 11월 7일에 열려요", { exact: true });
    await expect(period).toBeVisible();
    // 🚨 기호로 잇지 않는다 (디자인 시스템 §4)
    await expect(card.getByText(/~/)).toHaveCount(0);
    const notice = card.getByText(NOTICE, { exact: true });
    const basis = card.getByText("36개월 또래가 자주 찾는 놀이예요", { exact: true });
    expect((await period.boundingBox())!.y).toBeLessThan((await notice.boundingBox())!.y);
    expect((await notice.boundingBox())!.y).toBeLessThan((await basis.boundingBox())!.y);
  });
});

test.describe("필요한 재료 · 준비물 (#659)", () => {
  /** 05 개인화 줄 하나. 제안 문장으로 찾는다 — 탭을 먼저 골라야 그 묶음의 줄이 선다. */
  const personalRow = (page: Page, text: string) =>
    page.getByRole("tabpanel").locator(":scope > ul > li").filter({ hasText: text });

  test("식사는 필요한 재료, 놀이는 준비물로 이유 다음 · 근거 선 위에 서고, 비어 오면 영역이 없다", async ({
    page,
  }) => {
    await page.goto(SUGGESTIONS);

    await page.getByRole("tab", { name: "식사 3가지" }).click();
    const tofu = personalRow(page, "두부를 부쳐서");
    await expect(tofu).toHaveCount(1);
    const ingredients = tofu.getByRole("list", { name: "필요한 재료" });
    await expect(ingredients.getByRole("listitem")).toHaveText(["두부"]);
    // 🚨 읽기 전용이다 — 재료마다 체크박스가 없고, 고르는 칸은 제안 하나뿐이다
    await expect(tofu.getByRole("checkbox")).toHaveCount(1);
    await expect(
      tofu.getByRole("checkbox", { name: "두부를 부쳐서 한 조각 곁들여 보세요", exact: true }),
    ).toBeVisible();
    // 이유 다음, 근거 선 위다 — 근거가 아니라서 "사용한 기록" 목록에 섞이지 않는다
    const reasonY = (await tofu.getByText("추천 이유", { exact: true }).boundingBox())!.y;
    const itemsY = (await tofu.getByText("필요한 재료", { exact: true }).boundingBox())!.y;
    const evidence = tofu.getByText(/^사용한 기록 \d+건$/);
    expect(reasonY).toBeLessThan(itemsY);
    expect(itemsY).toBeLessThan((await evidence.boundingBox())!.y);
    await expect(
      evidence.locator("xpath=following-sibling::ul[1]").getByText("두부", { exact: true }),
    ).toHaveCount(0);

    await page.getByRole("tab", { name: "놀이 3가지" }).click();
    const pool = personalRow(page, "실내 물놀이장");
    await expect(pool.getByRole("list", { name: "준비물" }).getByRole("listitem")).toHaveCount(3);
    await expect(pool.getByText("필요한 재료", { exact: true })).toHaveCount(0);

    // 🚨 비어 온 줄은 라벨만 선 빈 영역이 없다
    const blocks = personalRow(page, "블록으로 높이 쌓기");
    await expect(blocks).toHaveCount(1);
    await expect(blocks.getByText(/^(준비물|필요한 재료)$/)).toHaveCount(0);
  });

  test("다섯 개부터 넷만 보이고, 펼치고 접어도 고른 상태는 그대로다", async ({ page }) => {
    await page.goto(SUGGESTIONS);
    await page.getByRole("tab", { name: "식사 3가지" }).click();
    const row = personalRow(page, "계란말이에 시금치를");
    const items = row.getByRole("list", { name: "필요한 재료" }).getByRole("listitem");
    const pick = row.getByRole("checkbox");
    await expect(items).toHaveCount(4);

    const more = row.getByRole("button", { name: "1개 더 보기" });
    await expect(more).toHaveAttribute("aria-expanded", "false");
    await more.click();
    await expect(items).toHaveCount(5);
    await expect(items.last()).toHaveText("애호박");
    const fold = row.getByRole("button", { name: "접기" });
    await expect(fold).toHaveAttribute("aria-expanded", "true");
    // 🚨 펼치기는 고르기가 아니다
    await expect(pick).not.toBeChecked();
    await expect(page.getByRole("button", { name: /가지로 할게요$/ })).toHaveCount(0);

    // 체크박스 표식은 숨긴 input 이라 라벨(제안 문장)을 누른다 — 보호자가 누르는 자리다
    await row.getByText("계란말이에 시금치를 조금 섞어 보세요", { exact: true }).click();
    await fold.click();
    await expect(items).toHaveCount(4);
    await expect(pick).toBeChecked();
    await expect(page.getByRole("button", { name: "이 1가지로 할게요" })).toBeVisible();
  });

  test("긴 준비물 이름은 칩 안에서 줄을 바꾸고 화면이 가로로 넘치지 않는다", async ({ page }) => {
    await page.setViewportSize({ width: 320, height: 720 });
    await page.goto(SUGGESTIONS);
    await page.getByRole("tab", { name: "놀이 3가지" }).click();
    const row = personalRow(page, "실내 물놀이장");
    const long = row
      .getByRole("listitem")
      .filter({ hasText: "유아용일회용방수기저귀겸수영팬티세트와여벌속옷두벌" });
    await expect(long).toBeVisible();

    const card = (await row.boundingBox())!;
    const chip = (await long.boundingBox())!;
    expect(chip.x + chip.width).toBeLessThanOrEqual(card.x + card.width);
    // 🚨 칩 상자는 `max-w-full` 로 카드 안에 들어도 글자가 상자 밖으로 나갈 수 있다 (카드가 `overflow-hidden` 이라
    //    문서 폭도 안 는다). 글자가 칩 안에 있고, 그러려고 칩이 두 줄이 됐는지를 본다
    const fits = await long.evaluate((el) => ({
      overflow: el.scrollWidth - el.clientWidth,
      lines: el.getBoundingClientRect().height / parseFloat(getComputedStyle(el).lineHeight),
    }));
    expect(fits.overflow).toBeLessThanOrEqual(0);
    // 한 줄 칩도 `min-h-chip`(28) 이라 caption 한 줄(16.8)의 1.67 배다 — 두 줄이어야 2 를 넘는다
    expect(fits.lines).toBeGreaterThanOrEqual(2);
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth),
    ).toBeLessThanOrEqual(0);
  });

  test.describe("일반 추천", () => {
    test.use({ scenario: "empty" });

    test("일반 식사 · 놀이도 같은 모양으로 기준 문구 다음 · 출처 위에 선다", async ({ page }) => {
      await page.goto(SUGGESTIONS);
      const meal = page
        .locator("li")
        .filter({ hasText: "국물 없이 집어 먹는 반찬을 한 가지 곁들여 보세요" })
        .filter({ has: page.getByText("일반 추천", { exact: true }) });
      await expect(meal).toHaveCount(1);
      await expect(
        meal.getByRole("list", { name: "필요한 재료" }).getByRole("listitem"),
      ).toHaveText(["애호박", "두부", "당근"]);
      const basisY = (await meal.getByText("36개월 또래의 식사에서 흔한 형태예요").boundingBox())!
        .y;
      const itemsY = (await meal.getByText("필요한 재료", { exact: true }).boundingBox())!.y;
      const sourceY = (await meal.getByText(/^참고 · /).boundingBox())!.y;
      expect(basisY).toBeLessThan(itemsY);
      expect(itemsY).toBeLessThan(sourceY);

      const play = page
        .locator("li")
        .filter({ hasText: "블록 쌓기처럼 손을 많이 쓰는 놀이" })
        .filter({ has: page.getByText("일반 추천", { exact: true }) });
      await expect(play.getByRole("list", { name: "준비물" }).getByRole("listitem")).toHaveCount(2);
      await expect(play.getByRole("checkbox")).toHaveCount(1);
    });
  });
});

test.describe("추천별 위험 경고 (#402 · #672)", () => {
  test.use({ scenario: "suggest_warnings" });

  // 글자는 서버 `apps/api/reference/hazard_terms.yaml` 의 `warning_text` 와 같다 (목 `hazardWarnings`)
  const WATER =
    "물놀이는 아이에게서 팔이 닿는 거리에서 한순간도 눈을 떼지 말아 주세요. 깊이 5cm 에서도 사고가 납니다.";
  const SMALL_PARTS =
    "작은 부품은 삼킬 수 있어요. 아이 입에 들어가지 않는 크기인지 먼저 확인해 주세요.";
  const BALLOON =
    "터진 풍선 조각과 비닐은 입에 넣거나 얼굴에 쓰지 않게 지켜봐 주세요. 놀이가 끝나면 손이 닿지 않는 곳에 치워 주세요.";

  test("개인화 줄은 추천 문장 아래, 이유보다 위에 세우고 사용한 기록에는 넣지 않는다", async ({
    page,
  }) => {
    await page.goto(SUGGESTIONS);
    await page.getByRole("tab", { name: "놀이 3가지" }).click();
    const panel = page.getByRole("tabpanel");
    const row = panel.locator(":scope > ul > li").filter({ hasText: "실내 물놀이장" });
    await expect(row).toHaveCount(1);

    const warnings = row.getByRole("list", { name: "주의할 점" });
    await expect(warnings.getByRole("listitem")).toHaveCount(1);
    const warning = warnings.getByText(WATER, { exact: true });
    await expect(warning).toBeVisible();
    // 🚨 고르는 칸의 이름은 제안 문장뿐이다
    await expect(
      row.getByRole("checkbox", { name: "주말에 실내 물놀이장은 어떨까요", exact: true }),
    ).toBeVisible();
    const reasonLabel = row.getByText("추천 이유", { exact: true });
    expect((await warning.boundingBox())!.y).toBeLessThan((await reasonLabel.boundingBox())!.y);

    // 🚨 경고는 근거가 아니다 — 근거 목록 안에 없고, 건수는 근거 줄 수 그대로다
    const label = row.getByText(/^사용한 기록 \d+건$/);
    const list = label.locator("xpath=following-sibling::ul[1]");
    const count = Number((await label.innerText()).match(/\d+/)![0]);
    await expect(list.locator("li")).toHaveCount(count);
    await expect(list.getByText(/눈을 떼지/)).toHaveCount(0);

    // 경고가 없는 줄에는 목록이 서지 않는다
    await expect(panel.getByRole("list", { name: "주의할 점" })).toHaveCount(1);
  });

  test("일반 카드는 경고 둘을 다 세우고, 기준 문구보다 위에 둔다", async ({ page }) => {
    await page.goto(SUGGESTIONS);
    const card = page.locator("li").filter({ hasText: "풍선 안에 작은 구슬을" });
    await expect(card).toHaveCount(1);
    await expect(card.getByText("일반 추천", { exact: true })).toBeVisible();
    // 기록 건수는 그 Agent 도메인 수 그대로다 — 경고가 붙어도 늘지 않는다
    await expect(card).toContainText("기록 3건");

    const warnings = card.getByRole("list", { name: "주의할 점" });
    await expect(warnings.getByRole("listitem")).toHaveCount(2);
    await expect(warnings.getByText(SMALL_PARTS, { exact: true })).toBeVisible();
    await expect(warnings.getByText(BALLOON, { exact: true })).toBeVisible();
    await expect(
      card.getByRole("checkbox", {
        name: "풍선 안에 작은 구슬을 넣고 흔들며 소리를 들어 보세요",
        exact: true,
      }),
    ).toBeVisible();

    const basis = card.getByText("36개월 또래가 자주 찾는 놀이예요", { exact: true });
    const last = warnings.getByText(BALLOON, { exact: true });
    expect((await last.boundingBox())!.y).toBeLessThan((await basis.boundingBox())!.y);
  });
});

test.describe("알레르기 정보가 없으면 식사 추천을 만들지 않는다 (§2 안전)", () => {
  test.use({ scenario: "stale" });

  test("막힌 쪽의 추천은 한 줄도 서지 않는다", async ({ page }) => {
    await page.goto(SUGGESTIONS);
    await expect(page.getByText("식사 추천을 만들지 않았어요")).toBeVisible();
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
    await expect(page.getByText("식사 추천을 만들지 않았어요")).toBeVisible();
    await expectNoGate(page);
    // 서버 deeplink(profile)를 주소로 쓰지 않는다 — 알레르기는 11 프로필에서 적는다.
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
    await expect(sheet.getByText("처음 보는 식품이 있어요")).toBeVisible();
    await expect(sheet.getByText("식품마다 하나씩 골라 주세요")).toBeVisible();
    await expect(sheet.getByRole("button", { name: "이대로 고르기" })).toBeDisabled();
    expect(writes(calls, "POST", /\/suggestions\/approve$/)).toEqual([]);

    // 🚨 되돌릴 수 없는 것 앞의 시트는 실수로 닫히지 않는다.
    await page.keyboard.press("Escape");
    await expect(sheet).toBeVisible();
  });

  test("칩은 답만 고른다 — 마음을 바꾸면 배너도 같이 바뀌고, 아무것도 저장되지 않는다 (#823)", async ({
    page,
  }) => {
    const calls = recordApi(page);
    const sheet = await pickEggAndAdopt(page);

    await sheet.getByRole("button", { name: "알레르기가 있어요" }).click();
    // 🚨 누르기 전에는 "추가해요" 다. "추가했어요" 는 서버 저장이 끝난 뒤에만 선다.
    await expect(sheet.getByText("계란을 알레르기 기록에 추가해요")).toBeVisible();
    await expect(sheet.getByText("알레르기 기록에 추가했어요", { exact: false })).toHaveCount(0);

    await sheet.getByRole("button", { name: "먹어 봤고 괜찮았어요" }).click();
    await expect(sheet.getByText("계란을 알레르기 기록에 추가해요")).toHaveCount(0);
    await expect(sheet.getByText("계란은 괜찮았던 식품으로 저장해요")).toBeVisible();
    await expect(sheet.getByRole("button", { name: "저장하고 고르기" })).toBeEnabled();
    expect(writes(calls, "POST", /\/health-safety$/)).toEqual([]);
  });

  test('"알레르기가 있어요" 는 버튼을 누를 때 저장되고, 고른 것이 전부 막히면 목록에서 알린다', async ({
    page,
  }) => {
    const calls = recordApi(page);
    const sheet = await pickEggAndAdopt(page);
    await sheet.getByRole("button", { name: "알레르기가 있어요" }).click();
    await expect(
      sheet.getByText("이번에는 고를 수 있는 추천이 없어요", { exact: false }),
    ).toBeVisible();

    // 🚨 이 버튼이 게이트 ㉡ 다 (칩에서 옮겼다 · 게이트 수는 그대로).
    expect(await gateMarks(page, "dialog[open]")).toContain("approve 버튼: 알레르기 저장하기");
    await sheet.getByRole("button", { name: "알레르기 저장하기" }).click();

    await expect(sheet).toBeHidden();
    const saved = writes(calls, "POST", /\/health-safety$/);
    expect(saved).toHaveLength(1);
    expect(saved[0].idempotencyKey).toBeTruthy();
    expect(saved[0].body).toMatchObject({ type: "allergy", label: "계란" });
    // s_1 만 골랐으므로 채택할 것이 남지 않는다. 🚨 조용히 풀지 않고 이유를 말한다.
    expect(writes(calls, "POST", /\/suggestions\/approve$/)).toEqual([]);
    await expect(
      page.getByRole("status").filter({ hasText: "계란이 들어간 추천은 고를 수 없어요" }),
    ).toBeVisible();
    await expect(page.getByRole("button", { name: "고른 것이 없어요" })).toBeDisabled();
  });

  test("고를 것이 남으면 저장이 끝난 뒤 나머지만 채택한다", async ({ page }) => {
    const calls = recordApi(page);
    await page.goto(SUGGESTIONS);
    await page.getByText("계란말이에 시금치를 조금 섞어 보세요").click();
    await page.getByRole("tab", { name: "놀이 3가지" }).click();
    await page.getByText("주말에 실내 물놀이장은 어떨까요").click();
    await page.getByRole("button", { name: "이 2가지로 할게요" }).click();

    const sheet = page.getByRole("dialog", { name: "확인해 주세요" });
    await sheet.getByRole("button", { name: "알레르기가 있어요" }).click();
    await sheet.getByRole("button", { name: "저장하고 나머지 고르기" }).click();

    await expect(page.getByText("이렇게 하기로 했어요")).toBeVisible();
    expect(writes(calls, "POST", /\/health-safety$/)).toHaveLength(1);
    const approved = writes(calls, "POST", /\/suggestions\/approve$/);
    expect(approved).toHaveLength(1);
    expect(approved[0].body).toEqual({ suggestion_ids: ["s_4"] });
  });

  test("다른 보호자가 먼저 등록한 식품이면 저장된 것으로 보고, 다시 시도가 없다 (#374)", async ({
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
    await sheet.getByRole("button", { name: "알레르기 저장하기" }).click();
    await expect(sheet).toBeHidden();
    await expect(
      page.getByRole("status").filter({ hasText: "계란이 들어간 추천은 고를 수 없어요" }),
    ).toBeVisible();
    await expect(page.getByText("저장하지 못했어요", { exact: false })).toHaveCount(0);
  });

  test('"모르겠어요" 는 아무것도 저장하지 않고, 그때 버튼은 게이트가 아니다', async ({ page }) => {
    const calls = recordApi(page);
    const sheet = await pickEggAndAdopt(page);
    await sheet.getByRole("button", { name: "모르겠어요" }).click();
    // 🚨 저장할 것이 없으면 이 버튼은 게이트가 아니다 — 표식이 서면 게이트가 는 것처럼 읽힌다.
    await expectNoGate(page, "dialog[open]");
    await sheet.getByRole("button", { name: "이대로 고르기" }).click();
    await expect(page.getByText("이렇게 하기로 했어요")).toBeVisible();
    expect(writes(calls, "POST", /\/health-safety$/)).toEqual([]);
  });

  test("놀이 추천이어도 먹을 것이 들어 있으면 묻는다 — 기준은 agent 가 아니다", async ({
    page,
  }) => {
    await page.goto(SUGGESTIONS);
    await page.getByRole("tab", { name: "놀이 3가지" }).click();
    await page.getByText("놀이터에 땅콩버터 쿠키를 간식으로 챙겨 가 보세요").click();
    await page.getByRole("button", { name: "이 1가지로 할게요" }).click();
    const sheet = page.getByRole("dialog", { name: "확인해 주세요" });
    // 🚨 제목은 식품 이름만이다 — 서버 note("알레르기 기록에 없음")는 저장한 뒤에도 남는 고정값이었다 (#823).
    await expect(sheet.getByText("땅콩", { exact: true })).toBeVisible();
    await expect(sheet.getByText("알레르기 기록에 없음", { exact: false })).toHaveCount(0);
  });

  test("목록을 받은 뒤 그사이 등록된 알레르기는 채택에서 서버가 막고, 화면은 그 추천을 빼고 알린다", async ({
    page,
  }) => {
    const calls = recordApi(page);
    await page.goto(SUGGESTIONS);
    await page.getByText("계란말이에 시금치를 조금 섞어 보세요").click();
    await page.getByText("두부를 부쳐서 한 조각 곁들여 보세요").click();
    await page.getByRole("button", { name: "이 2가지로 할게요" }).click();
    const sheet = page.getByRole("dialog", { name: "확인해 주세요" });

    // 시트에 답하는 사이 다른 보호자가 대두를 알레르기로 등록했다.
    expect(
      await asOtherCaregiver(page, {
        method: "POST",
        path: `/children/${CHILD}/health-safety`,
        body: { type: "allergy", label: "대두" },
        idempotent: true,
      }),
    ).toBe(201);
    for (const card of await sheet.locator("section").all()) {
      await card.getByRole("button", { name: "모르겠어요" }).click();
    }
    await sheet.getByRole("button", { name: "이대로 고르기" }).click();

    // 🚨 하나라도 걸리면 서버는 전부 거절한다 — 아무것도 채택되지 않았다.
    await expect(
      page.getByRole("status").filter({
        hasText: "그사이 알레르기로 등록된 식품이 들어간 추천이 있어서 고르지 못했어요",
      }),
    ).toBeVisible();
    await expect(page.getByText("이렇게 하기로 했어요")).toHaveCount(0);
    // 🚨 실패 카드가 아니다 — 같은 선택으로 다시 누르면 같은 409 라서 서버 문구를 실패로 세우지 않는다.
    await expect(page.getByText("알레르기로 등록된 항목이 들어 있어 고를 수 없어요")).toHaveCount(
      0,
    );
    expect(writes(calls, "POST", /\/suggestions\/approve$/)).toHaveLength(1);

    // 걸린 두부만 선택에서 빠졌다. 남은 계란말이로 다시 고르면 채택된다.
    await expect(page.getByRole("button", { name: "이 1가지로 할게요" })).toBeVisible();
    await page.getByRole("button", { name: "이 1가지로 할게요" }).click();
    await sheet.getByRole("button", { name: "이대로 고르기" }).click();
    await expect(page.getByText("이렇게 하기로 했어요")).toBeVisible();
    const approved = writes(calls, "POST", /\/suggestions\/approve$/);
    expect(approved.at(-1)?.body).toEqual({ suggestion_ids: ["s_1"] });
  });
});

test.describe("채택이 동의로 막히면 (#835)", () => {
  test("목록을 받은 뒤 건강정보 동의가 없어지면 일반 실패가 아니라 동의 카드를 세운다", async ({
    page,
  }) => {
    await page.goto(SUGGESTIONS);
    await page.getByText("계란말이에 시금치를 조금 섞어 보세요").click();
    await page.getByRole("button", { name: "이 1가지로 할게요" }).click();
    const sheet = page.getByRole("dialog", { name: "확인해 주세요" });
    await sheet.getByRole("button", { name: "모르겠어요" }).click();

    // 목록을 받은 뒤 동의를 거뒀다 — 알레르기 항목이 든 제안은 서버가 건강정보를 다시 읽어야 채택한다.
    await switchScenario(page, "consent");
    await sheet.getByRole("button", { name: "이대로 고르기" }).click();

    await expect(page.getByText("먼저 동의가 필요해요")).toBeVisible();
    await expect(page.getByText("고른 것을 남길 수 없어요", { exact: false })).toBeVisible();
    await expect(page.getByRole("link", { name: "동의 관리로 가기" })).toHaveAttribute(
      "href",
      `/child/${CHILD}/settings`,
    );
    // 🚨 다시 눌러도 같은 403 이라 일반 실패 카드(서버 문구 + "아직 아무것도 저장되지 않았어요")를 세우지 않는다.
    await expect(page.getByText("아직 아무것도 저장되지 않았어요")).toHaveCount(0);
    await expect(page.getByText("이렇게 하기로 했어요")).toHaveCount(0);
  });
});

/**
 * 채택한 뒤 등록된 알레르기 (#859). 채택은 새로고침해도 남고 "일정으로 만들기" 는 나중에 눌러도 되어서,
 * 그사이 다른 보호자가 등록한 알레르기는 초안 만들기(서버)가 다시 보고 `409 allergen_blocked` 로 막는다.
 * 🚨 다시 눌러도 같은 응답이라 일반 실패 카드(서버 문구 + "아직 아무것도 넣지 않았어요")를 세우지 않는다.
 */
test.describe("채택한 뒤 등록된 알레르기는 일정으로 만들지 않는다 (#859)", () => {
  /** 계란말이(계란)를 고르고 시트에서 "모르겠어요" 로 채택까지 간다. `extra` 는 함께 고를 놀이 제안. */
  async function approveEggDish(page: Page, extra?: string) {
    await page.goto(SUGGESTIONS);
    await page.getByText("계란말이에 시금치를 조금 섞어 보세요").click();
    if (extra) {
      await page.getByRole("tab", { name: "놀이 3가지" }).click();
      await page.getByText(extra).click();
    }
    await page
      .getByRole("button", { name: extra ? "이 2가지로 할게요" : "이 1가지로 할게요" })
      .click();
    const sheet = page.getByRole("dialog", { name: "확인해 주세요" });
    await sheet.getByRole("button", { name: "모르겠어요" }).click();
    await sheet.getByRole("button", { name: "이대로 고르기" }).click();
    await expect(page.getByText("이렇게 하기로 했어요")).toBeVisible();
  }

  const registerEgg = (page: Page) =>
    asOtherCaregiver(page, {
      method: "POST",
      path: `/children/${CHILD}/health-safety`,
      body: { type: "allergy", label: "계란" },
      idempotent: true,
    });

  test("걸린 추천만 빼고 알린 뒤, 다시 누르면 나머지로 초안을 만든다", async ({ page }) => {
    const calls = recordApi(page);
    await approveEggDish(page, "주말에 실내 물놀이장은 어떨까요");
    expect(await registerEgg(page)).toBe(201);

    await page.getByRole("button", { name: "일정으로 만들기" }).click();
    const notice = page.getByRole("status").filter({
      hasText: "그사이 알레르기로 등록된 식품이 들어간 추천이 있어서 일정으로 만들지 않았어요",
    });
    await expect(notice).toBeVisible();
    await expect(notice.getByText("식사", { exact: true })).toBeVisible();
    // 🚨 실패 카드가 아니다 — 서버 문구도 "아직 아무것도 넣지 않았어요" 도 세우지 않는다.
    await expect(
      page.getByText("알레르기로 등록된 항목이 들어 있어 일정으로 만들 수 없어요"),
    ).toHaveCount(0);
    await expect(page.getByText("아직 아무것도 넣지 않았어요")).toHaveCount(0);
    await expect(page.getByRole("dialog", { name: "캘린더에 넣을까요" })).toHaveCount(0);
    // 🚨 채택은 풀리지 않는다 — 서버에서 계속 approved 다.
    await expect(page.getByText("식사 1가지, 놀이 1가지")).toBeVisible();

    // 걸린 계란말이를 빼고 다시 보낸다 — 같은 id 로 다시 보내면 같은 409 다.
    await page.getByRole("button", { name: "일정으로 만들기" }).click();
    await expect(page.getByRole("dialog", { name: "캘린더에 넣을까요" })).toBeVisible();
    const drafts = writes(calls, "POST", /\/suggestions\/event-drafts$/);
    expect(drafts.map((d) => d.body)).toEqual([
      { suggestion_ids: ["s_1", "s_4"] },
      { suggestion_ids: ["s_4"] },
    ]);
    expect(writes(calls, "POST", /\/events$/)).toEqual([]);
  });

  test("묶음 안내는 초안에 실릴 것만 센다 — 막힌 식사 추천은 세지 않는다", async ({ page }) => {
    await page.goto(SUGGESTIONS);
    await page.getByText("계란말이에 시금치를 조금 섞어 보세요").click();
    await page.getByText("두부를 부쳐서 한 조각 곁들여 보세요").click();
    await page.getByRole("button", { name: "이 2가지로 할게요" }).click();
    const sheet = page.getByRole("dialog", { name: "확인해 주세요" });
    for (const card of await sheet.locator("section").all()) {
      await card.getByRole("button", { name: "모르겠어요" }).click();
    }
    await sheet.getByRole("button", { name: "이대로 고르기" }).click();
    await expect(page.getByText("이렇게 하기로 했어요")).toBeVisible();
    await expect(page.getByText("식사에서 고른 2가지는 일정 하나로 묶여요.")).toBeVisible();
    expect(await registerEgg(page)).toBe(201);

    await page.getByRole("button", { name: "일정으로 만들기" }).click();
    await expect(
      page.getByRole("status").filter({ hasText: "그 추천을 빼고 다시 만들 수 있어요" }),
    ).toBeVisible();
    // 🚨 남은 것은 두부 하나라 묶을 것이 없다. "고른 것" 요약은 채택을 그대로 센다.
    await expect(page.getByText("일정 하나로 묶여요", { exact: false })).toHaveCount(0);
    await expect(page.getByText("식사 2가지")).toBeVisible();
  });

  test("고른 것이 전부 걸렸으면 일정으로 만들기를 더 묻지 않는다", async ({ page }) => {
    const calls = recordApi(page);
    await approveEggDish(page);
    expect(await registerEgg(page)).toBe(201);

    await page.getByRole("button", { name: "일정으로 만들기" }).click();
    await expect(
      page.getByRole("status").filter({
        hasText: "그사이 알레르기로 등록된 식품이 들어간 추천이라 일정으로 만들 수 없어요",
      }),
    ).toBeVisible();
    // 🚨 만들 것이 없는데 버튼을 세우면 눌러도 같은 409 다.
    await expect(page.getByRole("button", { name: "일정으로 만들기" })).toHaveCount(0);
    await expect(page.getByText("일정으로도 만들까요?")).toHaveCount(0);
    await expect(page.getByText("이렇게 하기로 했어요")).toBeVisible();
    expect(writes(calls, "POST", /\/suggestions\/event-drafts$/)).toHaveLength(1);
  });

  test("채택한 뒤 건강정보 동의가 없어지면 일반 실패가 아니라 동의 카드를 세운다", async ({
    page,
  }) => {
    await approveEggDish(page);
    // 알레르기 항목이 든 제안은 서버가 건강정보를 다시 읽어야 초안을 만든다.
    await switchScenario(page, "consent");
    await page.getByRole("button", { name: "일정으로 만들기" }).click();

    await expect(page.getByText("먼저 동의가 필요해요")).toBeVisible();
    await expect(page.getByText("일정 초안을 만들 수 없어요", { exact: false })).toBeVisible();
    await expect(page.getByRole("link", { name: "동의 관리로 가기" })).toHaveAttribute(
      "href",
      `/child/${CHILD}/settings`,
    );
    await expect(page.getByText("아직 아무것도 넣지 않았어요")).toHaveCount(0);
  });

  /**
   * 초안을 만든 뒤 등록된 알레르기 (#902). 승인 시트는 열어 둔 채 나중에 넣을 수 있어서, 넣는 순간 제출(서버)이
   * 다시 보고 `409 allergen_blocked` 로 막는다. 제출은 카드 한 장에 요청 하나라 걸린 카드만 막힌다.
   */
  async function pickToday(sheet: Locator, page: Page) {
    await sheet.getByRole("button", { name: /언제/ }).click();
    await page.getByRole("button", { name: /^오늘, / }).click();
    await page.getByRole("button", { name: "고르기" }).click();
  }

  test("시트를 연 사이 등록된 알레르기는 그 카드만 잠그고, 다른 카드는 넣고, 닫으면 05 가 둘 다 다시 만들지 않는다 (#902)", async ({
    page,
  }) => {
    const calls = recordApi(page);
    await approveEggDish(page, "주말에 실내 물놀이장은 어떨까요");
    await page.getByRole("button", { name: "일정으로 만들기" }).click();
    const sheet = page.getByRole("dialog", { name: "캘린더에 넣을까요" });
    await expect(sheet.getByText("2건 중 1번째")).toBeVisible();
    expect(await registerEgg(page)).toBe(201);

    // 식사 초안 — 넣는 순간 서버가 막는다.
    await pickToday(sheet, page);
    const submit = sheet.getByRole("button", { name: "확인했어요, 캘린더에 넣을게요" });
    await submit.click();
    await expect(
      sheet.getByRole("status").filter({
        hasText: "그사이 알레르기로 등록된 식품이 들어간 추천이라 이 일정은 넣지 않았어요.",
      }),
    ).toBeVisible();
    // 🚨 다시 눌러도 같은 409 라 실패 카드(서버 문구 + 다시 누를 버튼)를 세우지 않는다.
    await expect(
      sheet.getByText("알레르기로 등록된 항목이 들어 있어 일정으로 만들 수 없어요"),
    ).toHaveCount(0);
    await expect(sheet.getByText("아직 아무것도 넣지 않았어요")).toHaveCount(0);
    await expect(submit).toHaveCount(0);

    // 🚨 막히는 것은 그 카드뿐이다 — 놀이 초안은 그대로 넣는다.
    await sheet.getByRole("button", { name: "다음" }).click();
    await pickToday(sheet, page);
    await sheet.getByRole("button", { name: "확인했어요, 캘린더에 넣을게요" }).click();
    await expect(sheet.getByText("넣었어요", { exact: true })).toBeVisible();
    expect(writes(calls, "POST", /\/children\/c1\/events$/).map((w) => w.body)).toEqual([
      expect.objectContaining({ suggestion_ids: ["s_1"] }),
      expect.objectContaining({ suggestion_ids: ["s_4"] }),
    ]);

    // 닫으면 초안 만들기의 409(#859)와 같은 안내가 서고, 채택은 그대로다.
    await sheet.getByRole("button", { name: "닫기" }).click();
    await expect(
      page.getByRole("status").filter({
        hasText: "그사이 알레르기로 등록된 식품이 들어간 추천이라 일정으로 만들 수 없어요",
      }),
    ).toBeVisible();
    await expect(page.getByText("식사 1가지, 놀이 1가지")).toBeVisible();
    // 🚨 넣은 놀이 추천은 다시 만들지 않는다 — 남은 것이 없으니 버튼 대신 넣었다는 말만 선다.
    await expect(page.getByText("넣을 수 있는 것은 캘린더에 넣었어요.")).toBeVisible();
    await expect(page.getByRole("button", { name: "일정으로 만들기" })).toHaveCount(0);
    await expect(page.getByText("일정으로도 만들까요?")).toHaveCount(0);
  });

  test("묶인 식사 초안이 막히면 닫고 다시 만들 때 걸린 추천만 빠진다 (#902)", async ({ page }) => {
    const calls = recordApi(page);
    await page.goto(SUGGESTIONS);
    await page.getByText("계란말이에 시금치를 조금 섞어 보세요").click();
    await page.getByText("두부를 부쳐서 한 조각 곁들여 보세요").click();
    await page.getByRole("button", { name: "이 2가지로 할게요" }).click();
    const check = page.getByRole("dialog", { name: "확인해 주세요" });
    for (const card of await check.locator("section").all()) {
      await card.getByRole("button", { name: "모르겠어요" }).click();
    }
    await check.getByRole("button", { name: "이대로 고르기" }).click();
    await page.getByRole("button", { name: "일정으로 만들기" }).click();
    const sheet = page.getByRole("dialog", { name: "캘린더에 넣을까요" });
    await expect(sheet.getByRole("textbox", { name: "제목" })).toHaveValue("저녁 식사");
    expect(await registerEgg(page)).toBe(201);

    await pickToday(sheet, page);
    await sheet.getByRole("button", { name: "확인했어요, 캘린더에 넣을게요" }).click();
    await expect(
      sheet.getByRole("status").filter({ hasText: "닫고 일정으로 다시 만들면 그 추천을 빼고" }),
    ).toBeVisible();

    await sheet.getByRole("button", { name: "닫기" }).click();
    await page.getByRole("button", { name: "일정으로 만들기" }).click();
    await expect(page.getByRole("dialog", { name: "캘린더에 넣을까요" })).toBeVisible();
    expect(writes(calls, "POST", /\/suggestions\/event-drafts$/).map((d) => d.body)).toEqual([
      { suggestion_ids: ["s_1", "s_2"] },
      { suggestion_ids: ["s_2"] },
    ]);
  });

  test("넣는 순간 건강정보 동의가 없으면 다시 넣을 길을 함께 말한다 (#902)", async ({ page }) => {
    await approveEggDish(page);
    await page.getByRole("button", { name: "일정으로 만들기" }).click();
    const sheet = page.getByRole("dialog", { name: "캘린더에 넣을까요" });
    await expect(sheet).toBeVisible();
    await switchScenario(page, "consent");

    await pickToday(sheet, page);
    const submit = sheet.getByRole("button", { name: "확인했어요, 캘린더에 넣을게요" });
    await submit.click();
    await expect(sheet.getByText("아직 아무것도 넣지 않았어요")).toBeVisible();
    await expect(
      sheet.getByText("설정 화면의 동의에서 켠 뒤 일정을 다시 만들어 주세요."),
    ).toBeVisible();
    // 다른 탭 · 기기에서 동의를 켰으면 같은 키로 들어가서 버튼은 남는다 (서버는 403 을 기억하지 않는다).
    await expect(submit).toBeEnabled();
  });
});

test.describe("승인 게이트 ㉡ — 같은 식품이 두 추천에 (#823)", () => {
  test.use({ scenario: "precheck_shared_item" });

  test("같은 식품은 한 장으로 묻고, 막으면 그 식품이 든 추천을 전부 막는다", async ({ page }) => {
    const calls = recordApi(page);
    await page.goto(SUGGESTIONS);
    await page.getByText("계란말이에 시금치를 조금 섞어 보세요").click();
    await page.getByText("두부를 부쳐서 한 조각 곁들여 보세요").click();
    await page.getByRole("button", { name: "이 2가지로 할게요" }).click();

    const sheet = page.getByRole("dialog", { name: "확인해 주세요" });
    await expect(sheet.getByText("계란", { exact: true })).toHaveCount(1);
    await expect(sheet.getByText("고른 추천 2개에 들어 있어요")).toBeVisible();

    const egg = sheet.locator("section").filter({ hasText: "고른 추천 2개에 들어 있어요" });
    await egg.getByRole("button", { name: "알레르기가 있어요" }).click();
    await sheet
      .locator("section")
      .filter({ hasText: "대두" })
      .getByRole("button", { name: "모르겠어요" })
      .click();
    await sheet.getByRole("button", { name: "알레르기 저장하기" }).click();

    // 두 제안 모두 계란이 들어 있어서 채택할 것이 없다.
    await expect(sheet).toBeHidden();
    expect(writes(calls, "POST", /\/suggestions\/approve$/)).toEqual([]);
    await expect(
      page.getByRole("status").filter({ hasText: "계란이 들어간 추천은 고를 수 없어요" }),
    ).toBeVisible();
  });
});

test.describe("승인 게이트 ㉡ — 저장 실패 (#823)", () => {
  test.use({ scenario: "safety_save_fail" });

  test("하나라도 저장에 실패하면 채택하지 않고, 실패한 것만 같은 키로 다시 보낸다", async ({
    page,
  }) => {
    const calls = recordApi(page);
    await page.goto(SUGGESTIONS);
    await page.getByText("계란말이에 시금치를 조금 섞어 보세요").click();
    await page.getByText("두부를 부쳐서 한 조각 곁들여 보세요").click();
    await page.getByText("멸치볶음을 간을 약하게 해서 조금만").click();
    await page.getByRole("button", { name: "이 3가지로 할게요" }).click();

    const sheet = page.getByRole("dialog", { name: "확인해 주세요" });
    for (const card of await sheet.locator("section").all()) {
      await card.getByRole("button", { name: "알레르기가 있어요" }).click();
    }
    await sheet.getByRole("button", { name: "저장하고 나머지 고르기" }).click();

    const failed = sheet.getByRole("status").filter({ hasText: "대두를 저장하지 못했어요" });
    await expect(failed).toBeVisible();
    await expect(failed).toContainText("계란은 저장했어요");
    // 🚨 실패 안내는 버튼 바로 위다 — 본문 맨 아래에 두면 고정된 아래 영역에 깔려 화면에서 안 보였다.
    await expect(failed).toBeInViewport();
    // 🚨 시트는 열린 채, 아무것도 채택되지 않았다. 저장된 계란은 답을 글자로 보이고 칩이 없다.
    await expect(sheet).toBeVisible();
    expect(writes(calls, "POST", /\/suggestions\/approve$/)).toEqual([]);
    const egg = sheet.locator("section").filter({ hasText: "계란" });
    await expect(egg.getByText("알레르기가 있다고 답했어요", { exact: false })).toBeVisible();
    await expect(egg.getByRole("button")).toHaveCount(0);
    expect(writes(calls, "POST", /\/health-safety$/)).toHaveLength(2);

    // 다시 시도는 아래 버튼 하나다.
    await sheet.getByRole("button", { name: "다시 저장하고 나머지 고르기" }).click();
    await expect.poll(() => writes(calls, "POST", /\/health-safety$/).length).toBe(3);
    await expect(failed).toBeVisible();

    // 🚨 다시 시도는 실패한 대두만, 처음과 같은 키로 보낸다. 계란을 한 번 더 넣지 않는다.
    const soy = writes(calls, "POST", /\/health-safety$/).filter(
      (c) => (c.body as { label: string }).label === "대두",
    );
    expect(soy).toHaveLength(2);
    expect(soy[1].idempotencyKey).toBe(soy[0].idempotencyKey);
    expect(writes(calls, "POST", /\/suggestions\/approve$/)).toEqual([]);
  });
});

test.describe("괜찮았던 식품 (#825)", () => {
  test('"괜찮았어요" 는 버튼을 누를 때 괜찮았던 식품으로 저장되고, 11 프로필에서 보고 뺄 수 있다', async ({
    page,
  }) => {
    const calls = recordApi(page);
    await page.goto(SUGGESTIONS);
    await page.getByText("계란말이에 시금치를 조금 섞어 보세요").click();
    await page.getByRole("button", { name: "이 1가지로 할게요" }).click();
    const sheet = page.getByRole("dialog", { name: "확인해 주세요" });
    await sheet.getByRole("button", { name: "먹어 봤고 괜찮았어요" }).click();

    // 🚨 괜찮았던 식품 저장도 게이트 ㉡ 다 — 건강 기록을 확정한다.
    expect(await gateMarks(page, "dialog[open]")).toContain("approve 버튼: 저장하고 고르기");
    await sheet.getByRole("button", { name: "저장하고 고르기" }).click();
    await expect(page.getByText("이렇게 하기로 했어요")).toBeVisible();

    const saved = writes(calls, "POST", /\/health-safety$/);
    expect(saved).toHaveLength(1);
    expect(saved[0].idempotencyKey).toBeTruthy();
    expect(saved[0].body).toMatchObject({ type: "allergy", label: "계란", tolerated: true });

    // 🚨 `page.goto` 로 가면 목 상태가 날아간다 — 앱 안의 길로 간다.
    await page.getByRole("link", { name: "홈으로" }).last().click();
    await page.getByRole("link", { name: "아이 프로필", exact: true }).click();

    const row = page.getByRole("button", { name: "괜찮았던 식품 1개" });
    await expect(row).toBeVisible();
    // 🚨 알레르기 목록과 섞이지 않는다. 목록이 뜬 뒤에 보고(받는 중에 보면 늘 0개다), 범위는 구역의 알레르기
    //    목록이다 — 닫힌 시트도 DOM 에 있어서 `main li` 로 보면 시트 안의 계란이 잡힌다.
    const allergies = page.locator('[data-tour="safety"] > ul');
    await expect(allergies.getByText("우유")).toBeVisible();
    await expect(allergies.getByText("계란")).toHaveCount(0);
    // 🚨 평가로 읽히는 색도, 게이트 표식도 없다.
    await expectNoGate(page);

    await row.click();
    const list = page.getByRole("dialog", { name: "괜찮았던 식품" });
    await list.getByRole("button", { name: "계란 빼기" }).click();
    await expect(list.getByText("계란을 빼면 다음 추천에서 다시 물어요.")).toBeVisible();
    // 🚨 빼면 더 묻는 쪽이라 빨강이 아니다. 보호자가 직접 누르는 삭제라 게이트 표식도 없다.
    expect(await gateMarks(page, "dialog[open]")).toEqual([]);
    await list.getByRole("button", { name: "뺄게요" }).click();

    await expect(row).toHaveCount(0);
    expect(writes(calls, "DELETE", /\/health-safety\/[^/]+$/)).toHaveLength(1);
  });

  test("그사이 같은 식품이 알레르기로 등록됐으면 알레르기가 이기고, 이유를 읽게 한 번 멈춘다", async ({
    page,
  }) => {
    const calls = recordApi(page);
    await page.goto(SUGGESTIONS);
    await page.getByText("계란말이에 시금치를 조금 섞어 보세요").click();
    await page.getByRole("button", { name: "이 1가지로 할게요" }).click();
    const sheet = page.getByRole("dialog", { name: "확인해 주세요" });

    expect(
      await asOtherCaregiver(page, {
        method: "POST",
        path: `/children/${CHILD}/health-safety`,
        body: { type: "allergy", label: "계란" },
        idempotent: true,
      }),
    ).toBe(201);
    await sheet.getByRole("button", { name: "먹어 봤고 괜찮았어요" }).click();
    await sheet.getByRole("button", { name: "저장하고 고르기" }).click();

    // 🚨 409 allergy_exists 는 실패가 아니다 — 다시 시도가 없고, 그 식품이 든 제안은 막는다.
    await expect(sheet.getByText("계란은 알레르기로 등록돼 있어요")).toBeVisible();
    await expect(sheet.getByText("저장하지 못했어요", { exact: false })).toHaveCount(0);
    expect(writes(calls, "POST", /\/suggestions\/approve$/)).toEqual([]);

    await sheet.getByRole("button", { name: "확인했어요" }).click();
    await expect(sheet).toBeHidden();
    await expect(
      page.getByRole("status").filter({ hasText: "계란이 들어간 추천은 고를 수 없어요" }),
    ).toBeVisible();
    expect(writes(calls, "POST", /\/suggestions\/approve$/)).toEqual([]);
  });
});

test.describe("괜찮았던 식품 — 답을 바꿔 다시 보낼 때 (#825)", () => {
  test.use({ scenario: "safety_save_fail" });

  test("알레르기로 보냈다 실패한 뒤 괜찮았어요로 바꾸면 다른 키로 보낸다", async ({ page }) => {
    const calls = recordApi(page);
    await page.goto(SUGGESTIONS);
    await page.getByText("두부를 부쳐서 한 조각 곁들여 보세요").click();
    await page.getByRole("button", { name: "이 1가지로 할게요" }).click();
    const sheet = page.getByRole("dialog", { name: "확인해 주세요" });

    await sheet.getByRole("button", { name: "알레르기가 있어요" }).click();
    await sheet.getByRole("button", { name: "알레르기 저장하기" }).click();
    await expect(sheet.getByText("대두를 저장하지 못했어요", { exact: false })).toBeVisible();

    await sheet.getByRole("button", { name: "먹어 봤고 괜찮았어요" }).click();
    await sheet.getByRole("button", { name: "다시 저장하고 고르기" }).click();
    await expect.poll(() => writes(calls, "POST", /\/health-safety$/).length).toBe(2);

    // 🚨 본문이 다르면 키도 다르다. 같은 키면 실서버는 처음 응답(알레르기)을 재생하거나 422 로 막는다.
    const [first, second] = writes(calls, "POST", /\/health-safety$/);
    expect(first.body).not.toHaveProperty("tolerated");
    expect(second.body).toMatchObject({ label: "대두", tolerated: true });
    expect(second.idempotencyKey).toBeTruthy();
    expect(second.idempotencyKey).not.toBe(first.idempotencyKey);
  });
});

test.describe("승인 게이트 ㉡ — 화면이 모르는 사전검사", () => {
  test.use({ scenario: "precheck_unknown_code" });

  test("묻지 못하는 검사가 걸린 추천은 채택하지 않는다", async ({ page }) => {
    const calls = recordApi(page);
    await page.goto(SUGGESTIONS);
    await page.getByRole("tab", { name: "놀이 3가지" }).click();
    await page.getByText("주말에 실내 물놀이장은 어떨까요").click();
    await page.getByRole("button", { name: "이 1가지로 할게요" }).click();

    // 🚨 예전에는 시트가 아무것도 묻지 않은 채 "확인했어요" 로 그대로 채택했다.
    const sheet = page.getByRole("dialog", { name: "확인해 주세요" });
    await expect(sheet.getByText("여기서 확인할 수 없는 항목이 있어요")).toBeVisible();
    // 고른 것이 전부 막혀서 고르기가 아니라 확인만 남는다.
    await sheet.getByRole("button", { name: "확인했어요" }).click();
    await expect(
      page
        .getByRole("status")
        .filter({ hasText: "확인이 필요한 항목 때문에 고른 추천을 고르지 않았어요" }),
    ).toBeVisible();
    expect(writes(calls, "POST", /\/suggestions\/approve$/)).toEqual([]);
    expect(writes(calls, "POST", /\/health-safety$/)).toEqual([]);
  });
});

test.describe("채택과 초안 만들기는 게이트가 아니다 · 승인 게이트 ㉠ 은 초안 제출", () => {
  test("놀이 추천 채택 → 일정으로 만들기 → 날짜를 고르고 넣을 때만 캘린더에 쓴다", async ({
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
    await expect(sheet.getByText("날짜를 골라 주셔야 넣을 수 있어요.")).toBeVisible();
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

  test("🚨 전에 넣지 않고 떠난 추천 초안은 시트에 서지 않고, 추천 초안은 보관함에 두지 않는다 (#539)", async ({
    page,
  }) => {
    const leftover = (draftId: string, title: string, suggestionIds?: string[]) => ({
      draft_id: draftId,
      op: "create",
      event_id: null,
      event: {
        title,
        starts_at: null,
        ends_at: null,
        all_day: true,
        event_type: "episodic",
        category: "etc",
      },
      before: null,
      items: [],
      ...(suggestionIds ? { suggestion_ids: suggestionIds } : {}),
    });
    await page.goto(SUGGESTIONS);
    // 지난번 05 에서 만들고 넣지 않은 제안 초안 + 04 에서 넣지 않은 초안. 보관함은 새로고침에도 남는다.
    await page.evaluate(
      ({ child, drafts }) =>
        sessionStorage.setItem(
          "icatch.event-drafts",
          JSON.stringify({ state: { byChild: { [child]: drafts } }, version: 0 }),
        ),
      {
        child: CHILD,
        drafts: [
          { origin: "suggestion", draft: leftover("old-suggestion", "지난번 추천", ["s_old"]) },
          { origin: "input", draft: leftover("old-input", "한 줄 입력 초안") },
        ],
      },
    );
    await page.reload();

    await page.getByRole("tab", { name: "놀이 3가지" }).click();
    await page.getByText("주말에 실내 물놀이장은 어떨까요").click();
    await page.getByRole("button", { name: "이 1가지로 할게요" }).click();
    await page.getByRole("button", { name: "일정으로 만들기" }).click();

    const sheet = page.getByRole("dialog", { name: "캘린더에 넣을까요" });
    await expect(sheet.getByRole("textbox", { name: "제목" })).toHaveValue(
      "주말에 실내 물놀이장은 어떨까요",
    );
    // 한 장뿐이라 넘기는 줄이 서지 않는다 — 지난 초안이 섞이면 "2건 중 1번째" 가 선다.
    await expect(sheet.getByText(/건 중/)).toHaveCount(0);

    // 🚨 제안 초안은 보관함에 두지 않는다 — 남은 것은 지우고, 이번 것도 얹지 않는다. 04 초안은 그대로다.
    const kept = await page.evaluate((child) => {
      const raw = sessionStorage.getItem("icatch.event-drafts");
      const state = raw ? JSON.parse(raw).state : { byChild: {} };
      return (state.byChild[child] ?? []).map(
        (d: { origin: string; draft: { draft_id: string } }) => [d.origin, d.draft.draft_id],
      );
    }, CHILD);
    expect(kept).toEqual([["input", "old-input"]]);
  });
});

test.describe("넣기 버튼은 시트 스크롤 바닥에 붙는다 (#866)", () => {
  /** 놀이 제안 하나로 초안 시트를 열고 준비물을 늘려 카드를 시트보다 길게 만든다 (재료가 그대로 들어오면 10개 안팎 · #679). */
  async function openLongDraft(page: Page) {
    await page.goto(SUGGESTIONS);
    await page.getByRole("tab", { name: "놀이 3가지" }).click();
    await page.getByText("주말에 실내 물놀이장은 어떨까요").click();
    await page.getByRole("button", { name: "이 1가지로 할게요" }).click();
    await page.getByRole("button", { name: "일정으로 만들기" }).click();

    const sheet = page.getByRole("dialog", { name: "캘린더에 넣을까요" });
    const add = sheet.getByRole("textbox", { name: "준비물 더하기" });
    for (let i = 1; i <= 10; i += 1) {
      await add.fill(`준비물 ${i}`);
      await add.press("Enter");
    }
    return { sheet, add };
  }

  /** 글자를 치는 칸을 스크롤 면 바닥에 둔다 — 브라우저는 입력 중인 칸을 바닥까지만 끌어올린다. 바가 그 칸을 덮는가. */
  async function typeAtBottom(add: Locator, text: string) {
    await add.fill(text);
    return add.evaluate((el) => {
      el.scrollIntoView({ block: "end" });
      const r = el.getBoundingClientRect();
      return document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2) !== el;
    });
  }

  test("🚨 준비물이 많아도 넣기 버튼과 막힌 이유가 스크롤 없이 보이고, 입력 중에는 칸을 덮지 않는다", async ({
    page,
  }) => {
    const { sheet, add } = await openLongDraft(page);

    // 🚨 입력하는 동안에는 바닥에 붙지 않는다 — 키보드가 올라온 시트에서 바가 입력칸을 덮는다.
    expect(await typeAtBottom(add, "쓰는 중")).toBe(false);
    // 🚨 옆의 "더하기" 를 누르는 순간 포커스가 옮겨 가도, 바가 그 자리를 덮어 누르기를 삼키지 않는다 (#866 리뷰).
    await sheet.getByRole("button", { name: "더하기" }).click();
    await expect(sheet.getByText("쓰는 중", { exact: true })).toBeVisible();
    await expect(add).toHaveValue("");
    await add.blur();

    // 시트를 연 첫 화면으로 되돌린다 — 더하는 동안 입력칸을 따라 내려갔다.
    await sheet.getByRole("textbox", { name: "제목" }).scrollIntoViewIfNeeded();
    await expect(sheet.getByRole("textbox", { name: "제목" })).toBeInViewport();

    const submit = sheet.getByRole("button", { name: "확인했어요, 캘린더에 넣을게요" });
    await expect(submit).toBeDisabled();
    await expect(submit).toBeInViewport({ ratio: 1 });
    // 🚨 꺼진 버튼만 보이고 이유가 가려지면 보호자는 왜 막혔는지 모른다.
    await expect(sheet.getByText("날짜를 골라 주셔야 넣을 수 있어요.")).toBeInViewport({
      ratio: 1,
    });
  });

  test.describe("손가락 탭", () => {
    test.use({ hasTouch: true });

    test('🚨 입력 중 바닥의 "더하기" 를 탭해도 준비물이 들어간다', async ({ page }) => {
      const { sheet, add } = await openLongDraft(page);

      expect(await typeAtBottom(add, "탭으로 더한 것")).toBe(false);
      await sheet.getByRole("button", { name: "더하기" }).tap();
      await expect(sheet.getByText("탭으로 더한 것", { exact: true })).toBeVisible();
      await expect(add).toHaveValue("");
    });
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

      const card = page.locator("li").filter({ has: page.getByText("일반 추천", { exact: true }) });
      await expect(card).toContainText("참고 · 블록 쌓기 (교육부, 표준보육과정)");
      // 🚨 문서는 아이 기록이 아니다 — "사용한 기록 N건" 으로 세지 않는다
      await expect(page.getByText(/^사용한 기록/)).toHaveCount(0);
      // 답하는 길(#246) 전에는 선택지가 비어 온다 — 문장으로만 서고 칩이 없다
      await expect(page.getByText("요즘 실내와 야외 중 어디를 더 찾나요?")).toBeVisible();
      await expect(page.getByRole("button", { name: "실내" })).toHaveCount(0);
    });
  });

  test.describe("요청에 맞춘 일반 추천 (#597)", () => {
    test.use({ scenario: "suggest_requested" });

    test("문서 출처 없이 기준 문구만 서고, 라벨은 또래 기준을 말하지 않는다", async ({ page }) => {
      await page.goto(SUGGESTIONS);

      const card = page.locator("li").filter({ has: page.getByText("일반 추천", { exact: true }) });
      await expect(card).toHaveCount(1);
      await expect(card).toContainText("기록 0건");
      await expect(card).toContainText("요청하신 놀이를 바탕으로 골랐어요.");
      // 🚨 요청이 기준이라 문서 행이 없다 — 출처 줄을 지어내지 않는다
      await expect(card.getByText(/^참고/)).toHaveCount(0);
      // 🚨 요청에 맞춰 고른 것을 또래 기준이라고 부르지 않는다 (카드 · 질문 카드 둘 다)
      await expect(page.getByText("또래", { exact: false })).toHaveCount(0);
      await expect(
        page.getByText("이번에는 아이 기록을 쓰지 않고, 추천마다 아래 적힌 기준으로 골랐어요.", {
          exact: false,
        }),
      ).toBeVisible();
      await expect(page.getByText("기록이 아직 적어서", { exact: false })).toHaveCount(0);
      // 아이 기록 근거도 없다 — 개인화로 그려지지 않는다
      await expect(page.getByText(/^사용한 기록/)).toHaveCount(0);
    });
  });

  test.describe("재료를 정해 물은 식사 일반 추천 (#740)", () => {
    test.use({ scenario: "suggest_requested_food" });

    test("레시피북 밖 메뉴도 출처 줄 없이 기준 문구만 서고, 라벨은 또래 기준을 말하지 않는다", async ({
      page,
    }) => {
      await page.goto(SUGGESTIONS);

      const card = page.locator("li").filter({ has: page.getByText("일반 추천", { exact: true }) });
      await expect(card).toHaveCount(1);
      await expect(card).toContainText("파프리카볶음");
      await expect(card).toContainText("기록 0건");
      await expect(card).toContainText("요청하신 조건을 바탕으로 골랐어요.");
      // 🚨 가리킬 메뉴 · 문서 행이 없다 — 출처 줄을 지어내지 않는다
      await expect(card.getByText(/^참고/)).toHaveCount(0);
      // 🚨 요청에 맞춰 고른 것을 또래 기준이라고 부르지 않는다
      await expect(page.getByText("또래", { exact: false })).toHaveCount(0);
      // 아이 기록 근거도 없다 — 개인화로 그려지지 않는다
      await expect(page.getByText(/^사용한 기록/)).toHaveCount(0);
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
