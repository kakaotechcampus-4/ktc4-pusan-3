import type { Page } from "@playwright/test";

import type { CalendarDayResponse } from "@/lib/api/types";

import { CHILD, expect, expectNoGate, recordApi, test, writes } from "./fixtures";
import { browserDaysAgo } from "./time-zone";

/**
 * 09 캘린더 — 서버(#318 1/3 · PR #325)가 주는 모양을 그대로 그린다 (#349).
 *
 * 🚨 **일기와 사진은 다른 저장소다** (#115). 일기는 쓴 보호자 본인 것만, 사진은 연결된 보호자가
 *    함께 본다. 그래서 사진만 있는 날이 생기고, 그날은 빈 날이 아니다.
 *
 * 목의 날짜는 지금부터 며칠 전이다 (`mocks/fixtures.ts`). 🚨 날짜는 `browserDaysAgo` 로 만든다 —
 * 러너 시간대(CI 는 UTC)로 세면 한국 시간 0시~9시에 하루 어긋나서 그 시각의 CI 만 실패한다.
 */

const CALENDAR = `/child/${CHILD}/calendar`;

test("사진만 있는 날은 빈 날이 아니다 — 사진이 서고 빈 상태가 서지 않는다", async ({ page }) => {
  // 목의 공유 사진 한 장은 7일 전, 그날은 기록도 일기도 일정도 없다.
  await page.goto(`${CALENDAR}?date=${browserDaysAgo(7)}`);

  await expect(page.getByRole("img", { name: "이날 캘린더에 남은 사진" })).toHaveCount(1);
  await expect(page.getByText("사진 있음")).toBeVisible();
  await expect(page.getByText("이날은 아무것도 없어요")).toHaveCount(0);
  // 🚨 사진은 보기만 한다 — 하루 패널에서 넣는 길을 두지 않는다 (#503).
  await expect(page.getByRole("button", { name: "사진으로 적기" })).toHaveCount(0);
});

test("내 일기는 일기 구역에 그대로 선다", async ({ page }) => {
  await page.goto(`${CALENDAR}?date=${browserDaysAgo(1)}`);

  await expect(page.getByText("저녁에 블록을 한참 쌓다가 무너지니까 웃었다.")).toBeVisible();
  await expect(page.getByRole("button", { name: "일기 고치기" })).toBeVisible();
});

test("일기 쓰기는 화면 머리에 있고, 쓰면 일기가 하루 패널 맨 위에 열린다 (#503)", async ({
  page,
}) => {
  // 목의 이틀 전은 기록이 있고 일기가 없는 날이다.
  await page.goto(`${CALENDAR}?date=${browserDaysAgo(2)}`);

  const title = page.getByRole("heading", { level: 1, name: "캘린더" });
  const records = page.getByRole("heading", { name: "이날 남긴 기록" });
  const write = page.getByRole("button", { name: "일기 쓰기" });
  await expect(records).toBeVisible();
  // 일기가 없는 날은 일기 구역이 서지 않는다 — 제목만 남은 빈 구역을 세우지 않는다.
  await expect(page.getByRole("heading", { name: "이날의 일기" })).toHaveCount(0);

  // 머리의 제목과 한 줄, 오른쪽이다.
  const [titleBox, writeBox] = await Promise.all([title.boundingBox(), write.boundingBox()]);
  expect(writeBox!.y).toBeLessThan(titleBox!.y + titleBox!.height);
  expect(writeBox!.x).toBeGreaterThan(titleBox!.x + titleBox!.width);

  await write.click();
  // 버튼과 편집기가 멀어서 편집기가 포커스를 받는다. 쓰는 동안 머리 버튼은 내려간다.
  await expect(page.getByRole("textbox", { name: "이날의 일기" })).toBeFocused();
  await expect(write).toHaveCount(0);
  const [diaryBox, recordsBox] = await Promise.all([
    page.getByRole("heading", { name: "이날의 일기" }).boundingBox(),
    records.boundingBox(),
  ]);
  expect(diaryBox!.y).toBeLessThan(recordsBox!.y);
});

test("편집기를 연 채 다른 날에 갔다 돌아오면 편집기는 닫혀 있다 (#503)", async ({ page }) => {
  const date = browserDaysAgo(2);
  await page.goto(`${CALENDAR}?date=${date}`);
  await page.getByRole("button", { name: "일기 쓰기" }).click();
  await expect(page.getByRole("textbox", { name: "이날의 일기" })).toBeVisible();

  // 같은 달의 다른 날로 갔다가 (고른 날 말고 첫 칸) 그날로 돌아온다.
  await page.getByRole("gridcell", { selected: false }).getByRole("button").first().click();
  await expect(page).not.toHaveURL(new RegExp(`date=${date}`));
  await page
    .getByRole("gridcell")
    .getByRole("button")
    .filter({ hasText: new RegExp(`^${Number(date.slice(8))}$`) })
    .click();
  await expect(page).toHaveURL(new RegExp(`date=${date}`));

  // 🚨 누르지도 않은 편집기가 다시 열리면 키보드가 올라온다.
  await expect(page.getByRole("button", { name: "일기 쓰기" })).toBeVisible();
  await expect(page.getByRole("textbox", { name: "이날의 일기" })).toHaveCount(0);
});

test("일기가 있는 날은 머리에 일기 쓰기가 서지 않는다 — 카드의 고치기가 그 일을 한다 (#503)", async ({
  page,
}) => {
  await page.goto(`${CALENDAR}?date=${browserDaysAgo(1)}`);

  await expect(page.getByRole("button", { name: "일기 고치기" })).toBeVisible();
  await expect(page.getByRole("button", { name: "일기 쓰기" })).toHaveCount(0);
});

test("건강 기록은 캘린더에 서지 않는다 (첫 배포 범위 밖 · #259 · #318)", async ({ page }) => {
  // 목의 건강 관찰은 어제 날짜다. 서버는 기록 탭과 같은 기준으로 캘린더에서도 뺀다.
  // ⚠️ 그래서 건강 기록의 상세 시트("진단 · 평가를 하지 않는다")를 여는 화면이 지금은 없다.
  //    건강이 범위로 돌아오면 그 화면에서 시트 테스트를 다시 건다.
  await page.goto(`${CALENDAR}?date=${browserDaysAgo(1)}`);

  await expect(page.getByText("저녁에 블록을 한참 쌓다가 무너지니까 웃었다.")).toBeVisible();
  await expect(page.getByText("자다가 기침을 몇 번 했어요")).toHaveCount(0);
});

test('범례에 "기억 변화" 가 없다 — 근거가 될 이력 표가 없다 (#318)', async ({ page }) => {
  await page.goto(CALENDAR);

  await expect(page.getByRole("listitem").filter({ hasText: /^일정$/ })).toBeVisible();
  await expect(page.getByText("기억 변화")).toHaveCount(0);
  await expect(page.getByRole("button", { name: /기억 달라짐/ })).toHaveCount(0);
});

/* ── 일기 쓰기 · 고치기 · 지우기 (#364 · 서버 #333) ─────────────────────── */

/**
 * 고른 날의 달력 칸. 🚨 칸의 라벨이 달 보기의 `has_my_diary` 를 말로 낸다 ("일기 있음").
 * 표식 모양은 `aria-hidden` 이라, 달 그리드의 일기 표식이 섰는지는 이 라벨로 잰다.
 */
function selectedDayButton(page: Page) {
  return page.getByRole("gridcell", { selected: true }).getByRole("button");
}

test("일기를 쓰고 고치고 지운다 — 새 경로로 키 없이 보내고, 그만두기는 아무것도 지우지 않는다 (#364)", async ({
  page,
}) => {
  // 7일 전은 사진만 있는 날이다 — 지운 뒤에도 사진이 남는지 같이 본다 (#115).
  const date = browserDaysAgo(7);
  const diaryPath = `/children/${CHILD}/diary/${date}`;
  const calls = recordApi(page);
  await page.goto(`${CALENDAR}?date=${date}`);

  const write = page.getByRole("button", { name: "일기 쓰기" });
  await expect(write).toBeVisible();
  await expect(selectedDayButton(page)).not.toHaveAccessibleName(/일기 있음/);

  // 쓰기
  await write.click();
  await page.getByRole("textbox", { name: "이날의 일기" }).fill("모래 놀이를 오래 했다.");
  await page.getByRole("button", { name: "일기 저장" }).click();

  await expect(page.getByText("모래 놀이를 오래 했다.")).toBeVisible();
  // 🚨 옛 경로(`PUT /calendar/{date}` · `{ text, event_ids }`)가 아니라 서버 #333 의 경로와 본문이다.
  //    Idempotency-Key 를 받지 않는 경로다 — 되돌릴 수 없는 5곳이 아니다.
  expect(writes(calls, "PUT")).toEqual([
    {
      method: "PUT",
      path: diaryPath,
      body: { content: "모래 놀이를 오래 했다." },
      idempotencyKey: undefined,
    },
  ]);
  // 달 그리드의 일기 표식이 선다 — 달 보기를 다시 받았다.
  await expect(selectedDayButton(page)).toHaveAccessibleName(/일기 있음/);

  // 고치기 — 줄바꿈까지 그대로 보낸다
  await page.getByRole("button", { name: "일기 고치기" }).click();
  const editor = page.getByRole("textbox", { name: "이날의 일기" });
  await expect(editor).toHaveValue("모래 놀이를 오래 했다.");
  await editor.fill("모래 놀이를 오래 했다.\n손을 두 번 씻었다.");
  await page.getByRole("button", { name: "일기 저장" }).click();

  await expect(page.getByText("손을 두 번 씻었다.")).toBeVisible();
  expect(writes(calls, "PUT").at(-1)?.body).toEqual({
    content: "모래 놀이를 오래 했다.\n손을 두 번 씻었다.",
  });

  // 지우기 확인 패널 → 그만두기: 요청이 나가지 않고 일기가 그대로다
  await page.getByRole("button", { name: "일기 지우기" }).click();
  await expect(page.getByText("이 일기를 지울까요?")).toBeVisible();
  await page.getByRole("button", { name: "그만두기" }).click();

  await expect(page.getByText("이 일기를 지울까요?")).toHaveCount(0);
  await expect(page.getByText("손을 두 번 씻었다.")).toBeVisible();
  expect(writes(calls, "DELETE")).toEqual([]);
  // 그만두기 버튼이 사라진 자리에서 포커스가 패널을 연 "일기 지우기" 로 돌아온다 (#364)
  await expect(page.getByRole("button", { name: "일기 지우기" })).toBeFocused();

  // 지우기 확정
  await page.getByRole("button", { name: "일기 지우기" }).click();
  const confirm = page.getByRole("button", { name: "지우기", exact: true });
  // 폰 화면 높이로 줄인다 — 넉넉한 화면이면 지운 뒤 문서가 화면보다 짧아져 스크롤이 0 으로 깎이고,
  // 그러면 머리로 튀었는지 잴 수 없다.
  await page.setViewportSize({ width: 390, height: 500 });
  await confirm.scrollIntoViewIfNeeded();
  await expect(write).not.toBeInViewport();
  await confirm.click();

  // 🚨 머리에 "일기 쓰기" 가 다시 서고, 누른 버튼이 구역째 사라졌으니 포커스가 그 버튼으로 온다.
  //    🚨 화면은 머리로 튀지 않는다 (`preventScroll` · #503).
  await expect(write).toBeVisible();
  await expect(write).toBeFocused();
  // 지운 일기만큼 문서가 짧아져 스크롤 위치는 조금 움직일 수 있다. 재는 것은 머리까지 올라갔는가다.
  await expect(write).not.toBeInViewport();
  await expect(page.getByText("손을 두 번 씻었다.")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "일기 고치기" })).toHaveCount(0);
  expect(writes(calls, "DELETE")).toEqual([
    { method: "DELETE", path: diaryPath, body: null, idempotencyKey: undefined },
  ]);
  // 달 그리드의 일기 표식이 사라지고, 사진은 그대로다 — 사진이 있으니 빈 날도 아니다.
  await expect(selectedDayButton(page)).not.toHaveAccessibleName(/일기 있음/);
  await expect(page.getByRole("img", { name: "이날 캘린더에 남은 사진" })).toHaveCount(1);
  await expect(page.getByText("이날은 아무것도 없어요")).toHaveCount(0);
});

test("고치다가 내용을 다 비우면 저장이 꺼지고, 편집 중에는 지우기가 없다 — 빈 저장은 지우기가 아니다 (#364)", async ({
  page,
}) => {
  const calls = recordApi(page);
  await page.goto(`${CALENDAR}?date=${browserDaysAgo(1)}`);

  await page.getByRole("button", { name: "일기 고치기" }).click();
  const editor = page.getByRole("textbox", { name: "이날의 일기" });
  const save = page.getByRole("button", { name: "일기 저장" });

  // 먼저 저장이 켜질 수 있는 상태에 닿는다 — 안 그러면 "꺼져 있다" 가 늘 참이다.
  await editor.fill("바꿔 적은 일기");
  await expect(save).toBeEnabled();

  // 🚨 공백 · 줄바꿈뿐이면 서버가 422 로 거절한다 (#333). 버튼이 먼저 막는다.
  await editor.fill("  \n ");
  await expect(save).toBeDisabled();
  // 꺼진 이유와 지우는 길을 글자로 말한다.
  await expect(save).toHaveAccessibleDescription(/비운 채로는 저장되지 않아요/);
  // 🚨 편집 중에는 지우기를 세우지 않는다 — 고치던 글을 버리는 것과 저장된 일기를 지우는 것이 섞인다.
  await expect(page.getByRole("button", { name: "일기 지우기" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "취소" })).toBeVisible();

  expect(writes(calls)).toEqual([]);
});

test("일기 지우기 확인 패널은 승인 게이트가 아니다 — 게이트 표식이 없다 (§2 실행 · #364)", async ({
  page,
}) => {
  const calls = recordApi(page);
  await page.goto(`${CALENDAR}?date=${browserDaysAgo(1)}`);

  await page.getByRole("button", { name: "일기 지우기" }).click();
  // 🚨 패널이 열린 것을 먼저 확인한다 — 닫힌 채로 재면 늘 통과한다.
  await expect(page.getByText("이 일기를 지울까요?")).toBeVisible();
  await expect(page.getByRole("button", { name: "지우기", exact: true })).toBeVisible();

  // 게이트는 캘린더 쓰기와 건강 기록 확정 딱 2곳이다. 자기 글을 지우는 확인은 그 칸이 아니다.
  await expectNoGate(page);
  expect(writes(calls)).toEqual([]);
});

/* ── 준비물 체크 (#367 · 서버 #335) ─────────────────────────────────────── */

test("준비물 체크는 아이 경로로 키 없이 나가고, 다시 받은 하루 보기에도 남는다 (#367)", async ({
  page,
}) => {
  // 목의 일정은 이틀 뒤다. 준비물 "수건" 은 아직 안 챙긴 상태로 시작한다.
  const date = browserDaysAgo(-2);
  const dayPath = `/children/${CHILD}/calendar/${date}`;
  const calls = recordApi(page);
  await page.goto(`${CALENDAR}?date=${date}`);

  const towel = page.getByRole("checkbox", { name: "수건" });
  // 🚨 눈에 보이는 줄(`label`)을 누른다. 체크박스는 `sr-only` 라 `force` 로 누르면 좌표만 찍혀서,
  //    일정 구역이 하단 네비 뒤에 걸리는 날(일기가 맨 위에 서고 나서 · #503) 네비의 "홈" 이 눌린다.
  const towelRow = page.locator("label").filter({ has: towel });
  await expect(towel).not.toBeChecked();
  // 체크가 끝나면 하루 보기를 다시 받는다 (`PreparedCheckbox` 의 onSettled).
  const reread = page.waitForResponse(
    (res) =>
      res.request().method() === "GET" && new URL(res.url()).pathname === `/api/v1${dayPath}`,
  );

  await towelRow.click();

  // 🚨 옛 경로(`PATCH /event-items/{iid}`)가 아니라 서버 #335 의 아이 경로다.
  //    Idempotency-Key 를 받지 않는 경로다 — 되돌릴 수 있는 체크라 되돌릴 수 없는 5곳이 아니다.
  await expect
    .poll(() => writes(calls, "PATCH"))
    .toEqual([
      {
        method: "PATCH",
        path: `/children/${CHILD}/event-items/i_1`,
        body: { is_prepared: true },
        idempotencyKey: undefined,
      },
    ]);
  // 🚨 다시 받은 값에도 체크가 남아 있다 — 화면의 체크가 낙관적 표시만이 아니다.
  const day = (await (await reread).json()) as CalendarDayResponse;
  expect(day.events.flatMap((event) => event.items).find((i) => i.item_id === "i_1")).toMatchObject(
    { is_prepared: true },
  );
  await expect(towel).toBeChecked();

  // 해제도 같은 경로로 최종 상태를 보낸다.
  await towelRow.click();
  await expect
    .poll(() => writes(calls, "PATCH").map((c) => c.body))
    .toEqual([{ is_prepared: true }, { is_prepared: false }]);
  await expect(towel).not.toBeChecked();

  // 되돌릴 수 있는 체크라 게이트 표식이 없다 (§2 실행).
  await expectNoGate(page);
});
