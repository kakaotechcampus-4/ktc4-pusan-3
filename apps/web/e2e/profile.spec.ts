import {
  CHILD,
  EVALUATIVE,
  asOtherCaregiver,
  expect,
  expectNoGate,
  gateMarks,
  recordApi,
  test,
  visibleText,
  writes,
} from "./fixtures";

/**
 * 11 아이 프로필 — 알레르기는 보호자만 확정하고(게이트 ㉡), 키 · 몸무게는 평가 없이 보여준다.
 */

const PROFILE = `/child/${CHILD}/profile`;

async function openAddSheet(page: import("@playwright/test").Page) {
  await page.goto(PROFILE);
  await page.getByRole("button", { name: "알레르기·건강 기록 추가" }).click();
  await page.getByRole("button", { name: /^직접 적기/ }).click();
  const sheet = page.getByRole("dialog", { name: "알레르기 · 건강 기록 추가" });
  // 시트는 아래에서 올라온다 (#490). 올라오는 중에 누르거나 휠을 굴리면 엉뚱한 자리에 맞는다.
  await sheet.evaluate((el) => Promise.all(el.getAnimations().map((a) => a.finished)));
  return sheet;
}

test.describe("알레르기 직접 등록은 승인 게이트 ㉡ (§2 안전)", () => {
  test("게이트 표식(caution 배너 · approve 버튼)이 서고 실수로 닫히지 않는다", async ({ page }) => {
    const sheet = await openAddSheet(page);
    await expect(sheet.getByText("보호자가 확인한 것만 적어주세요")).toBeVisible();
    const marks = await gateMarks(page, "dialog[open]");
    expect(marks).toContain("approve 버튼: 확인했어요, 등록할게요");
    expect(marks.some((m) => m.startsWith("caution 색:"))).toBe(true);

    await page.keyboard.press("Escape");
    await expect(sheet).toBeVisible();
    await sheet.getByRole("button", { name: "그만두기" }).click();
    await expect(sheet).toBeHidden();
  });

  test("Idempotency-Key 를 달고, 서버가 확정하기 전에는 목록에 먼저 그리지 않는다", async ({
    page,
  }) => {
    const calls = recordApi(page);
    const sheet = await openAddSheet(page);
    await sheet.getByLabel("무엇인가요").fill("땅콩");

    const list = page.locator("main").getByText("땅콩", { exact: true });
    await sheet.getByRole("button", { name: "확인했어요, 등록할게요" }).click();
    // 🚨 낙관적 업데이트 금지 — 응답 전에 목록에 서면 확정된 것처럼 보인다 (apps/web §3 에러).
    await expect(sheet.getByText("등록하는 중이에요")).toBeVisible();
    await expect(list).toHaveCount(0);

    await expect(sheet).toBeHidden();
    await expect(list).toBeVisible();
    const saved = writes(calls, "POST", /\/health-safety$/);
    expect(saved).toHaveLength(1);
    expect(saved[0].idempotencyKey).toBeTruthy();
    // 심각도를 고르지 않았으면 싣지 않는다 — "모르겠어요" 를 값으로 지어내지 않는다.
    expect(saved[0].body).not.toHaveProperty("severity");
  });

  /**
   * ⚠️ **키가 큰 화면에서 돌린다.** 390×844 에서는 심각도 고르기 상자의 목록이 시트 본문 바닥에
   *    가려져서(목록 y 573~830 · 본문 바닥 703) 네 번째 항목부터 눌리지 않는다 — 항목을 보이게
   *    하려고 본문이 스크롤되는 순간 `Select` 가 닫힌다. `Select` 자체의 문제라 이 테스트가
   *    덮지 않는다.
   */
  test.describe("종류에 따라 심각도 선택지가 갈린다", () => {
    test.use({ viewport: { width: 390, height: 1400 } });

    test("알레르기는 검사 등급(Class)을 묻고, 보내는 값은 DB 가 받는 값이다", async ({ page }) => {
      const calls = recordApi(page);
      const sheet = await openAddSheet(page);

      await sheet.getByRole("combobox", { name: "검사 등급" }).click();
      await expect(page.getByRole("option")).toHaveText([
        "모르겠어요",
        "Class 0",
        "Class 1",
        "Class 2",
        "Class 3",
        "Class 4",
        "Class 5",
        "Class 6",
      ]);
      await page.getByRole("option", { name: "Class 3" }).click();
      await sheet.getByLabel("무엇인가요").fill("복숭아");
      await sheet.getByRole("button", { name: "확인했어요, 등록할게요" }).click();
      await expect(sheet).toBeHidden();

      const saved = writes(calls, "POST", /\/health-safety$/);
      expect(saved).toHaveLength(1);
      expect(saved[0].body).toMatchObject({
        type: "allergy",
        label: "복숭아",
        severity: "class_3",
      });
    });

    // Class 는 검사지를 처음 보면 무엇인지 모른다. 툴팁이 아니라 늘 보이는 한 줄이다 —
    // 웹뷰에는 호버가 없다 (#570).
    test("알레르기 검사 등급에는 설명이 붙고, 상자가 그 설명을 읽는다 — 지병으로 바꾸면 빠진다", async ({
      page,
    }) => {
      const sheet = await openAddSheet(page);
      const hint = "검사지에 적힌 Class 를 그대로 골라요. 몇 등급이든 식단 추천에서는 똑같이 빼요.";
      await expect(sheet.getByText(hint)).toBeVisible();
      await expect(sheet.getByRole("combobox", { name: "검사 등급" })).toHaveAccessibleDescription(
        hint,
      );

      await sheet.getByRole("combobox", { name: "종류" }).click();
      await page.getByRole("option", { name: "지병 · 만성질환" }).click();
      await expect(sheet.getByText(hint)).toHaveCount(0);
      // 설명이 없는 상자는 `aria-describedby` 를 비우지 않고 아예 걸지 않는다.
      await expect(sheet.getByRole("combobox", { name: "얼마나 심한가요" })).not.toHaveAttribute(
        "aria-describedby",
      );
    });

    test("지병으로 바꾸면 선택지가 바뀌고, 앞서 고른 Class 는 남지 않는다", async ({ page }) => {
      const sheet = await openAddSheet(page);
      await sheet.getByRole("combobox", { name: "검사 등급" }).click();
      await page.getByRole("option", { name: "Class 3" }).click();

      await sheet.getByRole("combobox", { name: "종류" }).click();
      await page.getByRole("option", { name: "지병 · 만성질환" }).click();

      // Class 3 이 남아 있으면 DB 가 막는 조합이 된다
      const severity = sheet.getByRole("combobox", { name: "얼마나 심한가요" });
      await expect(severity).toContainText("모르겠어요");
      await severity.click();
      await expect(page.getByRole("option")).toHaveText(["모르겠어요", "가볍게", "보통", "심하게"]);
    });
  });
});

/**
 * 시트 본문은 스크롤되는 면이라, 아래쪽 고르기 상자의 목록이 본문 바닥에 잘린다. 열 때 목록이
 * 보이게 본문을 움직이고, 열린 뒤의 스크롤에는 닫지 않아야 잘린 항목까지 고를 수 있다 (#306).
 */
test.describe("시트 안 고르기 상자는 마지막 항목까지 고를 수 있다 (#306)", () => {
  test("열면 목록이 끝까지 보이고, 마지막 항목이 등록 요청에 실린다", async ({ page }) => {
    const calls = recordApi(page);
    const sheet = await openAddSheet(page);
    // 지병으로 고른다 — 심각도 상자의 이름과 마지막 값("심하게")이 알레르기와 달리 바뀌지 않는다 (#298).
    await sheet.getByRole("combobox", { name: "종류" }).click();
    await page.getByRole("option", { name: "지병 · 만성질환" }).click();
    await sheet.getByLabel("무엇인가요").fill("천식");

    await sheet.getByRole("combobox", { name: "얼마나 심한가요" }).click();
    const last = page.getByRole("option").last();
    await expect(last).toHaveText("심하게");
    await expect(last).toBeInViewport({ ratio: 1 });
    await last.click();
    await expect(page.getByRole("listbox")).toHaveCount(0);

    await sheet.getByRole("button", { name: "확인했어요, 등록할게요" }).click();
    await expect(sheet).toBeHidden();
    const saved = writes(calls, "POST", /\/health-safety$/);
    expect(saved).toHaveLength(1);
    expect(saved[0].body).toMatchObject({ label: "천식", severity: "severe" });
  });

  test("열린 채로 시트 본문을 스크롤해도 닫히지 않는다", async ({ page }) => {
    const sheet = await openAddSheet(page);
    // 알레르기(기본값)면 상자 이름이 "검사 등급" 이다 — 위 테스트처럼 지병으로 바꿔 이름을 고정한다 (#298).
    await sheet.getByRole("combobox", { name: "종류" }).click();
    await page.getByRole("option", { name: "지병 · 만성질환" }).click();
    const trigger = sheet.getByRole("combobox", { name: "얼마나 심한가요" });
    await trigger.click();
    await expect(page.getByRole("listbox")).toBeVisible();

    // 본문이 실제로 움직였는지부터 본다 — 안 움직였으면 이 테스트는 아무것도 확인하지 않는다.
    const body = sheet.locator(".overflow-y-auto").first();
    const before = await body.evaluate((el) => el.scrollTop);
    await trigger.hover();
    await page.mouse.wheel(0, 80);
    await expect.poll(() => body.evaluate((el) => el.scrollTop)).toBeGreaterThan(before);

    await expect(page.getByRole("listbox")).toBeVisible();
    await expect(trigger).toHaveAttribute("aria-expanded", "true");
  });
});

test.describe("기본 정보 고치기는 서버 범위 안에서만 받는다 (#347)", () => {
  test("별명은 20자에서 더 들어가지 않는다", async ({ page }) => {
    await page.goto(PROFILE);
    await page.getByRole("button", { name: "기본 정보 고치기" }).click();
    const sheet = page.getByRole("dialog", { name: "기본 정보 고치기" });
    const nickname = sheet.getByLabel("별명");
    await nickname.clear();
    // 서버는 21자부터 400 이다. 막히지 않으면 저장 버튼이 계속 실패한다.
    await nickname.pressSequentially("가".repeat(25));
    await expect(nickname).toHaveValue("가".repeat(20));
  });
});

test.describe("고르는 칸의 체크는 글자 바로 옆에 선다 (#490)", () => {
  /** 첫 줄의 글자 범위 · 체크 · 칸. 두 줄로 꺾인 라벨은 첫 줄만 본다 — 체크가 서는 줄이다. */
  async function measure(label: import("@playwright/test").Locator) {
    return label.evaluate((el) => {
      const text = [...el.querySelector("span")!.childNodes].find(
        (node) => node.nodeType === Node.TEXT_NODE,
      )!;
      const range = document.createRange();
      range.selectNodeContents(text);
      const firstLine = range.getClientRects()[0]!;
      const icon = el.querySelector("svg")!.getBoundingClientRect();
      const box = el.getBoundingClientRect();
      return {
        lines: new Set([...range.getClientRects()].map((r) => Math.round(r.top))).size,
        box: { left: box.left, center: box.left + box.width / 2 },
        firstLine: { left: firstLine.left, center: firstLine.left + firstLine.width / 2 },
        icon: { left: icon.left, right: icon.right },
      };
    });
  }

  for (const name of ["여자아이", "밝히지 않을래요"]) {
    test(`"${name}" — 글자는 칸 가운데에, 체크는 첫 줄 글자 바로 왼쪽에 선다`, async ({ page }) => {
      await page.goto(PROFILE);
      await page.getByRole("button", { name: "기본 정보 고치기" }).click();
      const sheet = page.getByRole("dialog", { name: "기본 정보 고치기" });
      await sheet.locator("label", { hasText: name }).click();
      await expect(sheet.getByRole("radio", { name })).toBeChecked();

      const m = await measure(sheet.locator("label", { hasText: name }));
      // 🚨 글자가 체크 때문에 밀리지 않는다 — 고를 때 바뀌는 것은 색뿐이다.
      expect(Math.abs(m.box.center - m.firstLine.center)).toBeLessThan(1);
      // 🚨 체크가 칸 가장자리가 아니라 글자에 붙는다 (간격 `mr-1.5` = 6px). 꺾인 라벨도 같다.
      expect(m.firstLine.left - m.icon.right).toBeCloseTo(6, 0);
      expect(m.icon.left).toBeGreaterThan(m.box.left);
    });
  }

  test('"밝히지 않을래요" 는 390px 에서 두 줄로 꺾인다 — 위 검사가 꺾인 칸을 실제로 본다', async ({
    page,
  }) => {
    await page.goto(PROFILE);
    await page.getByRole("button", { name: "기본 정보 고치기" }).click();
    const sheet = page.getByRole("dialog", { name: "기본 정보 고치기" });
    const m = await measure(sheet.locator("label", { hasText: "밝히지 않을래요" }));
    expect(m.lines).toBe(2);
  });
});

test.describe("알레르기 등록과 내리기는 서버 응답대로 그린다 (#374)", () => {
  test("같은 본문을 다시 누르면 같은 키, 이름을 고쳐 다시 누르면 새 키다", async ({ page }) => {
    const calls = recordApi(page);
    const sheet = await openAddSheet(page);
    const name = sheet.getByLabel("무엇인가요");
    const submit = sheet.getByRole("button", { name: "확인했어요, 등록할게요" });

    // 우유는 이미 등록돼 있다 (목 픽스처) — 409 로 시트가 남는다.
    await name.fill("우유");
    await submit.click();
    await expect(sheet.getByText("이미 등록된 항목이에요", { exact: false })).toBeVisible();
    await submit.click();
    await expect(sheet.getByText("이미 등록된 항목이에요", { exact: false })).toBeVisible();

    // 🚨 서버는 같은 키면 본문을 비교하지 않고 처음 응답을 재생한다 — 고친 이름은 새 키여야 저장된다.
    await name.fill("땅콩");
    await submit.click();
    await expect(sheet).toBeHidden();
    await expect(page.locator("main").getByText("땅콩", { exact: true })).toBeVisible();

    const saved = writes(calls, "POST", /\/health-safety$/);
    expect(saved.map((c) => (c.body as { label: string }).label)).toEqual(["우유", "우유", "땅콩"]);
    expect(saved[1].idempotencyKey).toBe(saved[0].idempotencyKey);
    expect(saved[2].idempotencyKey).not.toBe(saved[0].idempotencyKey);
  });

  test("이름과 메모는 서버가 받는 길이에서 더 들어가지 않고, 증상은 개수를 넘기면 막는다", async ({
    page,
  }) => {
    const calls = recordApi(page);
    const sheet = await openAddSheet(page);
    const name = sheet.getByLabel("무엇인가요");
    await name.pressSequentially("가".repeat(105));
    await expect(name).toHaveValue("가".repeat(100));
    const notes = sheet.getByLabel("메모 · 선택");
    await notes.fill("나".repeat(505));
    await expect(notes).toHaveValue("나".repeat(500));

    await sheet
      .getByLabel("증상 · 선택")
      .fill(Array.from({ length: 21 }, (_, n) => `증상${n}`).join(", "));
    await sheet.getByRole("button", { name: "확인했어요, 등록할게요" }).click();
    await expect(sheet.getByText("증상은 20개까지 적을 수 있어요")).toBeVisible();
    expect(writes(calls, "POST", /\/health-safety$/)).toEqual([]);
  });

  test("다른 보호자가 먼저 내린 기록을 내리면 실패가 아니라 목록에서 빠진다", async ({ page }) => {
    await page.goto(PROFILE);
    const milk = page.locator("main li").filter({ hasText: "우유" });
    await milk.getByRole("button", { name: "이 기록 내리기" }).click();

    expect(
      await asOtherCaregiver(page, {
        method: "DELETE",
        path: `/children/${CHILD}/health-safety/hs_1`,
      }),
    ).toBe(204);

    await milk.getByRole("button", { name: "내릴게요" }).click();
    await expect(milk).toHaveCount(0);
    // 🚨 "아직 안전 정보에 그대로 있으니 다시 눌러 주세요" 는 사실과 반대말이다 — 이미 빠졌다.
    await expect(page.getByText("내리지 못했어요", { exact: false })).toHaveCount(0);
  });
});

test.describe("알레르기 고치기는 게이트 ㉡ 이고, 고치면 새 기록이다 (#375)", () => {
  async function openEditSheet(page: import("@playwright/test").Page) {
    await page.goto(PROFILE);
    await page
      .locator("main li")
      .filter({ hasText: "우유" })
      .getByRole("button", { name: "이 기록 고치기" })
      .click();
    return page.getByRole("dialog", { name: "알레르기 · 건강 기록 고치기" });
  }

  test("그 기록의 값으로 열리고, Idempotency-Key 를 달고 고친다", async ({ page }) => {
    const calls = recordApi(page);
    const sheet = await openEditSheet(page);
    // 목 픽스처의 우유: Class 3, 두드러기
    await expect(sheet.getByRole("combobox", { name: "검사 등급" })).toContainText("Class 3");
    await expect(sheet.getByLabel("증상 · 선택")).toHaveValue("두드러기");

    await sheet.getByLabel("메모 · 선택").fill("병원에서 다시 확인");
    await sheet.getByRole("button", { name: "확인했어요, 고칠게요" }).click();
    await expect(sheet).toBeHidden();
    await expect(page.locator("main").getByText("병원에서 다시 확인")).toBeVisible();

    const edits = writes(calls, "PATCH", /\/health-safety\/hs_1$/);
    expect(edits).toHaveLength(1);
    expect(edits[0].idempotencyKey).toBeTruthy();
    // 🚨 종류와 이름은 싣지 않는다 — 그 둘은 이 기록의 정체다.
    expect(edits[0].body).not.toHaveProperty("type");
    expect(edits[0].body).not.toHaveProperty("label");
  });

  test("다른 보호자가 먼저 고친 기록이면 다시 누르라고 하지 않고, 닫으면 지금 기록이 보인다", async ({
    page,
  }) => {
    const sheet = await openEditSheet(page);
    expect(
      await asOtherCaregiver(page, {
        method: "PATCH",
        path: `/children/${CHILD}/health-safety/hs_1`,
        body: { notes: "다른 보호자가 먼저 적음" },
        idempotent: true,
      }),
    ).toBe(200);

    await sheet.getByLabel("메모 · 선택").fill("내가 적으려던 것");
    await sheet.getByRole("button", { name: "확인했어요, 고칠게요" }).click();
    await expect(
      sheet.getByText("이미 먼저 고쳐졌거나 내려간 기록이에요", { exact: false }),
    ).toBeVisible();
    // 🚨 다시 눌러도 같은 404 다 — 누를 길을 남기지 않는다.
    await expect(sheet.getByRole("button", { name: "확인했어요, 고칠게요" })).toBeDisabled();
    await expect(sheet.getByText("다시 눌러 주세요", { exact: false })).toHaveCount(0);

    await sheet.getByRole("button", { name: "그만두기" }).click();
    await expect(sheet).toBeHidden();
    await expect(page.locator("main").getByText("다른 보호자가 먼저 적음")).toBeVisible();
    await expect(page.locator("main").getByText("내가 적으려던 것")).toHaveCount(0);
  });
});

test.describe("키 · 몸무게는 최근 한 줄만, 평가 없이 (§2 개인정보)", () => {
  test("프로필에는 그래프도 평가 낱말도 없다", async ({ page }) => {
    await page.goto(PROFILE);
    await expect(page.getByText("키 104.2cm, 몸무게 17.1kg")).toBeVisible();
    await expect(page.locator(".recharts-wrapper")).toHaveCount(0);
    // 직전 값과의 차이(+1.2cm)도 변화를 평가하는 말이 된다.
    const text = await visibleText(page);
    expect(text).not.toMatch(EVALUATIVE);
    expect(text).not.toMatch(/[+-]\d+(\.\d+)?\s?(cm|kg)/);
  });

  test("측정 기록 새로 적기는 승인 게이트가 아니다", async ({ page }) => {
    await page.goto(PROFILE);
    await page.getByRole("button", { name: "키·몸무게 새로 적기" }).click();
    const sheet = page.getByRole("dialog", { name: "새로 재서 적기" });
    await expect(sheet).toBeVisible();
    await expectNoGate(page, "dialog[open]");
    await page.keyboard.press("Escape");
    await expect(sheet).toBeHidden();
  });
});
