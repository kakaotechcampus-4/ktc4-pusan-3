import { CHILD, EVALUATIVE, expect, test, visibleText } from "./fixtures";

/**
 * 11-1 키 · 몸무게 상세 — 보호자가 넣은 숫자를 그대로 그린다 (최상위 CLAUDE.md §2 개인정보).
 *
 * 🚨 백분위 · 또래 비교 · 표준 성장곡선 겹치기 · "빠르다/느리다/정상" 같은 평가 표현(**색 포함**)을
 *    만들지 않는다. Recharts 는 jsdom 에서 크기가 0 이라 그려지지 않아서 이 검사는 브라우저로만 건다.
 */

const GROWTH = `/child/${CHILD}/profile/growth`;

test.beforeEach(async ({ page }) => {
  await page.goto(GROWTH);
  await expect(page.locator("figure .recharts-line-curve")).toHaveCount(2);
});

test("기준선 · 범위 · 범례 · 두 번째 선이 없다", async ({ page }) => {
  for (const forbidden of [
    ".recharts-reference-line",
    ".recharts-reference-area",
    ".recharts-reference-dot",
    ".recharts-area",
    ".recharts-legend-wrapper",
  ]) {
    await expect(page.locator(forbidden), forbidden).toHaveCount(0);
  }
  for (const figure of await page.locator("figure").all()) {
    await expect(figure.locator(".recharts-line-curve")).toHaveCount(1);
    await expect(figure.locator(".recharts-yAxis")).toHaveCount(1);
  }
});

test("선과 점은 뉴트럴 토큰으로만 칠한다 — 평가로 읽히는 색이 없다", async ({ page }) => {
  const offending = await page.evaluate(() => {
    const css = getComputedStyle(document.documentElement);
    const rgb = (name: string) => {
      const n = parseInt(css.getPropertyValue(name).trim().replace("#", ""), 16);
      return `rgb(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255})`;
    };
    const allowed = new Set(
      [
        "--color-ink",
        "--color-ink-muted",
        "--color-ink-subtle",
        "--color-line",
        "--color-line-strong",
        "--color-surface",
      ].map(rgb),
    );
    const found: string[] = [];
    // 실제로 칠해지는 도형만 본다. `g` 는 상속값(검정)을 들고 있을 뿐 칠하지 않고,
    // `defs` · `clipPath` 안은 화면에 그려지지 않는다.
    const shapes = document.querySelectorAll<SVGElement>(
      "figure svg :is(path, circle, line, rect, text, tspan, polyline, polygon, ellipse)",
    );
    for (const el of shapes) {
      if (el.closest("defs, clipPath")) continue;
      const style = getComputedStyle(el);
      const paints = el.tagName === "line" ? [style.stroke] : [style.stroke, style.fill];
      for (const paint of paints) {
        if (paint === "none" || paint === "rgba(0, 0, 0, 0)" || paint === "") continue;
        if (!allowed.has(paint))
          found.push(`${el.tagName}.${el.getAttribute("class") ?? ""} ${paint}`);
      }
    }
    return found;
  });
  expect(offending).toEqual([]);
});

test("화면 어디에도 평가 낱말이 없다 (평가하지 않는다는 안내 한 줄 말고는)", async ({ page }) => {
  const notice = "잰 날 그대로 쌓아 둬요. 또래와 견주거나 백분위를 매기지 않아요.";
  await expect(page.getByText(notice)).toBeVisible();
  const text = (await visibleText(page)).replace(notice, "");
  expect(text).not.toMatch(EVALUATIVE);
});

test("툴팁은 값과 날짜만 보여주고 직전과의 차이를 만들지 않는다", async ({ page }) => {
  const dots = page.locator("figure").first().locator(".recharts-line-dots circle");
  await dots.last().hover({ force: true });
  const tooltip = page.locator(".recharts-tooltip-wrapper").first();
  await expect(tooltip).toContainText("cm");
  const text = await tooltip.innerText();
  expect(text).not.toMatch(/[+-]\s?\d|지난번|늘었|줄었/);
  expect(text).not.toMatch(EVALUATIVE);
});
