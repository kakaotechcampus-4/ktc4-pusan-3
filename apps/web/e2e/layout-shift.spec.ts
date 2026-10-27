import type { Page } from "@playwright/test";

import type { Scenario } from "@/mocks/scenario";

import { CHILD, expect, test } from "./fixtures";

/**
 * 화면이 뜨는 동안 레이아웃이 밀리지 않는다 (CLS). 스켈레톤이 받은 내용과 **같은 상자**를 차지하는지
 * (`components/ui/skeleton.tsx`) · 손글씨가 늦게 붙어 줄바꿈이 바뀌지 않는지(`app/layout.tsx` 의 서체 미리 받기)를 건다.
 *
 * 브라우저의 Layout Instability API(`layout-shift`)로 잰다 — Lighthouse · Chrome 의 CLS 와 같은 값이다.
 * 사용자 입력 직후 0.5초의 이동은 빼고, 1초 간격 · 최대 5초 창 중 가장 큰 합을 CLS 로 친다 (web.dev 정의).
 *
 * 🚨 **실기기 수치가 아니다.** 목은 응답을 220ms 안팎으로 한꺼번에 주고 개발 서버로 돈다. 그래서 이 값은
 *    "받은 순간 무엇이 움직이는가" 를 잡는 회귀 검사이고, 기준은 Google 의 "좋음" 경계(0.1)다.
 *    실서버에서 응답이 따로따로 오면 이동이 더 갈릴 수 있다 — 실측은 `docs/web/mock-and-tests-v1.md` §3.
 * 🚨 실패하면 메시지에 **움직인 요소와 위치**가 찍힌다. 기준을 올리기 전에 그 요소의 스켈레톤부터 본다.
 */

/** Google 의 CLS "좋음" 경계. */
const GOOD_CLS = 0.1;

type Shift = {
  value: number;
  startTime: number;
  hadRecentInput: boolean;
  sources: string[];
};

async function recordLayoutShifts(page: Page) {
  await page.addInitScript(() => {
    const shifts: Shift[] = [];
    (window as unknown as { __shifts: Shift[] }).__shifts = shifts;
    new PerformanceObserver((list) => {
      for (const entry of list.getEntries() as Array<
        PerformanceEntry & {
          value: number;
          hadRecentInput: boolean;
          sources?: Array<{
            node?: Node;
            previousRect: DOMRectReadOnly;
            currentRect: DOMRectReadOnly;
          }>;
        }
      >) {
        shifts.push({
          value: entry.value,
          startTime: entry.startTime,
          hadRecentInput: entry.hadRecentInput,
          sources: (entry.sources ?? []).map((source) => {
            const node = source.node instanceof Element ? source.node : null;
            const name = node
              ? `${node.tagName.toLowerCase()}${node.className ? `.${String(node.className).split(" ").slice(0, 3).join(".")}` : ""}`
              : "?";
            return `${name} y ${Math.round(source.previousRect.y)}→${Math.round(source.currentRect.y)}, h ${Math.round(source.previousRect.height)}→${Math.round(source.currentRect.height)}`;
          }),
        });
      }
    }).observe({ type: "layout-shift", buffered: true });
  });
}

async function measureCls(page: Page): Promise<{ cls: number; detail: string }> {
  const shifts = await page.evaluate(() => (window as unknown as { __shifts: Shift[] }).__shifts);
  let cls = 0;
  let windowSum = 0;
  let windowStart = 0;
  let previous = Number.NEGATIVE_INFINITY;
  for (const shift of shifts.filter((s) => !s.hadRecentInput)) {
    if (shift.startTime - previous > 1000 || shift.startTime - windowStart > 5000) {
      windowSum = 0;
      windowStart = shift.startTime;
    }
    windowSum += shift.value;
    previous = shift.startTime;
    cls = Math.max(cls, windowSum);
  }
  const detail = shifts
    .filter((s) => s.value > 0.001)
    .map((s) => `+${s.value.toFixed(3)} @${Math.round(s.startTime)}ms\n  ${s.sources.join("\n  ")}`)
    .join("\n");
  return { cls, detail };
}

/**
 * 화면마다 **받은 뒤에만 서는 문구** — 이게 보이면 스켈레톤이 내용으로 바뀐 뒤다.
 *
 * 🚨 `ready` 는 **스켈레톤에 없는 문구**여야 한다. 스켈레톤도 값이 아닌 글자(구역 이름 · 그래프 제목)는 그대로
 * 그려서, 그런 문구를 고르면 받기 전에 재고 끝나 늘 통과한다.
 * 🚨 **빈 상태(`empty`) 줄을 빼지 않는다.** 줄 모양으로 기다린 자리에 빈 상태가 서는 경로이고, 02 처럼 빈 상태
 * 모양으로 기다리는 화면(`expectEmpty`)은 거기서만 제 경로를 탄다.
 */
const SCREENS: Array<{ name: string; path: string; ready: string; scenario?: Scenario }> = [
  { name: "03 홈", path: `/child/${CHILD}/home`, ready: "눈여겨볼 것" },
  { name: "07 기록", path: `/child/${CHILD}/memories`, ready: "저녁에 계란말이를 또 찾았어요" },
  {
    name: "07 기억",
    path: `/child/${CHILD}/memories?tab=profile`,
    ready: "서로 다른 3일에 기록됐어요",
  },
  {
    name: "07 제안 피드백",
    path: `/child/${CHILD}/memories?tab=feedback`,
    ready: "계란말이에 시금치를 조금 섞어 보세요",
  },
  { name: "09 캘린더", path: `/child/${CHILD}/calendar`, ready: "이날의 일기 쓰기" },
  { name: "10 설정", path: `/child/${CHILD}/settings`, ready: "함께 보고 있어요" },
  { name: "11 아이 프로필", path: `/child/${CHILD}/profile`, ready: "키 104.2cm, 몸무게 17.1kg" },
  {
    name: "11-1 키 · 몸무게",
    path: `/child/${CHILD}/profile/growth`,
    ready: "키 104.2cm, 몸무게 17.1kg",
  },
  {
    name: "05 제안 후보",
    path: `/child/${CHILD}/suggestions?agents=food,activity`,
    ready: "추천 이유",
  },
  { name: "01 첫 진입", path: "/onboarding", ready: "민감정보 처리 (아이 건강·알레르기)" },
  { name: "02 아이 정보", path: `/child/${CHILD}/onboarding`, ready: "우유" },
  {
    name: "03 홈 · 기록 0건",
    path: `/child/${CHILD}/home`,
    ready: "아래에 한 줄 적으면 여기에 쌓여요",
    scenario: "empty",
  },
  {
    name: "11 아이 프로필 · 0건",
    path: `/child/${CHILD}/profile`,
    ready: "등록된 것이 아직 없어요",
    scenario: "empty",
  },
  {
    name: "11-1 키 · 몸무게 · 0건",
    path: `/child/${CHILD}/profile/growth`,
    ready: "아직 잰 기록이 없어요",
    scenario: "empty",
  },
  {
    name: "02 아이 정보 · 알레르기 0건",
    path: `/child/${CHILD}/onboarding`,
    ready: "등록된 것이 아직 없어요",
    scenario: "empty",
  },
];

for (const screen of SCREENS) {
  test.describe(() => {
    if (screen.scenario) test.use({ scenario: screen.scenario });

    test(`${screen.name} — 받는 동안과 받은 뒤의 자리가 같다 (CLS < ${GOOD_CLS})`, async ({
      page,
    }) => {
      await recordLayoutShifts(page);
      await page.goto(screen.path);
      await expect(page.getByText(screen.ready, { exact: false }).first()).toBeVisible();
      // 받은 뒤에 따라 서는 것(서체 · 늦게 오는 두 번째 응답)까지 기다린다.
      await page.waitForTimeout(1000);

      const { cls, detail } = await measureCls(page);
      expect(cls, `CLS ${cls.toFixed(3)} — 움직인 요소:\n${detail}`).toBeLessThan(GOOD_CLS);
    });
  });
}
