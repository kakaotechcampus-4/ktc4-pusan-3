import type { Locator, Page } from "@playwright/test";

import {
  CHILD,
  expect,
  expectNoGate,
  gateMarks,
  recordApi,
  switchScenario,
  test,
  writes,
} from "./fixtures";
import { browserDaysAgo } from "./time-zone";

/**
 * 08 사진 확인 화면 — 서버가 읽은 날짜 칸을 화면이 그대로 쓰는가 (#723), 고른 일정을 버튼 하나로
 * 넣고 저장하는가 (#824).
 *
 * 목의 알림장(`mocks/handlers/photos.ts` 의 `noticeEntries`)은 서버 모양을 흉내 낸다 —
 * 시각을 읽은 일정(도서관 나들이 9:30), 여러 날 일정(독서 주간), 마감만 있는 할 일(동의서 회신),
 * 날짜 없는 안내(비 오는 날 실내 활동), 시각 있는 시작에 하루 종일 마감이 붙은 일정(부모 참여 수업),
 * 시각 있는 마감(가정통신문 회신), 날짜를 못 읽은 일정(생일잔치)이 한 장에 있다.
 * 시작 날짜를 읽은 일정은 다섯이다 — 도서관 나들이 · 독서 주간 · 부모 참여 수업 · 원복 입고 등원 · 물놀이.
 * 지난 일정(가을 운동회 · 서버가 `past` 로 표시)도 하나 있는데, 이것은 켜 두지 않는다 (#828 리뷰).
 * 마감이 지난 할 일(현장학습 신청서 회신)도 `past` 지만, 할 일이라 원래 켜지 않는다.
 */

const EVENTS = /\/children\/c1\/events$/;
const COMMIT = /\/photo-runs\/[^/]+\/commit$/;

async function openNotice(
  page: Page,
  scenario?:
    "photo_meal_plan" | "photo_event_partial" | "photo_commit_failed" | "photo_commit_expired",
): Promise<void> {
  await page.goto(`/child/${CHILD}/home`);
  if (scenario) await switchScenario(page, scenario);
  await page.getByRole("button", { name: "사진으로 적기" }).click();
  await page
    .getByRole("button", { name: /기기에 있는 최근 사진/ })
    .first()
    .click();
  await expect(page.getByText("알림장이나 식단표로 읽었어요.")).toBeVisible({ timeout: 15_000 });
  await page.getByRole("button", { name: /^잘 읽었어요/ }).click();
}

function row(page: Page, title: string): Locator {
  return page.getByRole("listitem").filter({ hasText: title }).first();
}

/** 그 줄의 "캘린더에 넣기" 체크. */
function pick(page: Page, title: string): Locator {
  return row(page, title).getByRole("checkbox", { name: "캘린더에 넣기" });
}

/** 그 줄의 "캘린더에 넣기" 를 누른다. 🚨 체크 칸은 숨긴 input 이라 글자(라벨)를 누른다 (`Checkbox`). */
async function togglePick(page: Page, title: string): Promise<void> {
  await row(page, title).getByText("캘린더에 넣기", { exact: true }).click();
}

async function openSheet(page: Page, title: string): Promise<Locator> {
  await row(page, title).getByRole("button", { name: "고치기" }).click();
  return page.getByRole("dialog");
}

/** 맨 아래 "이렇게 저장할게요" 카드. */
function summary(page: Page): Locator {
  return page
    .locator("div")
    .filter({ has: page.getByText("이렇게 저장할게요") })
    .last();
}

test("목록 — 여러 날은 기간, 할 일은 마감, 날짜 없는 안내에는 날짜 줄이 없다", async ({ page }) => {
  await openNotice(page);

  await expect(row(page, "독서 주간")).toContainText(/언제.*부터.*까지/);
  // 🚨 읽은 시각은 오전 · 오후와 함께 보인다 — 표시 없는 시각을 오전으로 읽은 것을 보호자가 확인한다.
  await expect(row(page, "도서관 나들이")).toContainText("오전 9:30");
  await expect(row(page, "체험학습 동의서 회신")).toContainText("마감");
  await expect(row(page, "체험학습 동의서 회신")).not.toContainText("언제");

  // 🚨 안내는 날짜가 없는 것이 정상이다. 못 읽은 값처럼 보이면 보호자가 없던 날을 고치러 간다.
  const info = row(page, "비 오는 날 실내 활동");
  await expect(info).not.toContainText("못 읽었어요");
  await expect(info).not.toContainText("언제");

  // 날짜를 정말 못 읽은 일정은 그대로 말한다.
  await expect(row(page, "생일잔치")).toContainText("못 읽었어요");
});

test("🚨 고치기 버튼은 고치기만 한다 — 시트에 캘린더에 넣는 버튼이 없다 (#824)", async ({
  page,
}) => {
  await openNotice(page);
  // 줄마다 버튼 문구가 같다. 일정 줄이라고 캘린더를 약속하지 않는다 — 넣을지는 체크가 말한다.
  await expect(row(page, "도서관 나들이").getByRole("button")).toHaveText("고치기");

  const sheet = await openSheet(page, "도서관 나들이");
  await expect(sheet.getByRole("heading", { name: "읽은 내용 고치기" })).toBeVisible();
  await expect(sheet.getByText("캘린더에 넣을 때")).toBeVisible();
  await expect(sheet.getByRole("button", { name: /캘린더에 넣/ })).toHaveCount(0);
  await expect(sheet.getByText("일정으로도 넣기")).toHaveCount(0);
  // 🚨 게이트 표식은 시트를 떠났다 — 맨 아래 버튼이 가져간다.
  expect(await gateMarks(page, "dialog[open]")).toEqual([]);
});

test("시작 날짜를 읽은 일정만 처음부터 켜져 있고, 할 일과 확인할 것은 잠겨 있다", async ({
  page,
}) => {
  await openNotice(page);

  for (const title of [
    "도서관 나들이",
    "독서 주간",
    "부모 참여 수업",
    "원복 입고 등원",
    "물놀이",
  ]) {
    await expect(pick(page, title), title).toBeChecked();
  }
  // 🚨 할 일은 보호자가 고른다. 마감일로 일정을 넣지 않으려고, 시작하는 날을 고르기 전에는 잠근다.
  await expect(pick(page, "체험학습 동의서 회신")).not.toBeChecked();
  await expect(pick(page, "체험학습 동의서 회신")).toBeDisabled();
  await expect(row(page, "체험학습 동의서 회신")).toContainText(
    "고치기에서 시작하는 날을 고르면 넣을 수 있어요.",
  );
  // 🚨 확인이 필요한 줄은 확인하기 전에 넣을 수 없다 — 저장이 그 항목을 받지 않는다.
  await expect(pick(page, "생일잔치")).toBeDisabled();
  await expect(row(page, "생일잔치")).toContainText("고치기에서 확인하면 넣을 수 있어요.");
  // 안내에는 체크가 아예 없다.
  await expect(row(page, "비 오는 날 실내 활동").getByRole("checkbox")).toHaveCount(0);
});

test("🚨 지난 일정은 꺼진 채로 시작하고 까닭을 말한다 — 켜면 넣는다 (#828 리뷰)", async ({
  page,
}) => {
  const calls = recordApi(page);
  await openNotice(page);

  await expect(pick(page, "가을 운동회")).not.toBeChecked();
  await expect(pick(page, "가을 운동회")).toBeEnabled();
  await expect(row(page, "가을 운동회")).toContainText("지난 일정이라 꺼 두었어요.");
  await expect(summary(page)).not.toContainText("가을 운동회");

  // 🚨 마감이 지난 할 일에는 이 말이 없다 — 할 일은 원래 켜지 않아서 틀린 까닭이다.
  await expect(row(page, "현장학습 신청서 회신")).not.toContainText("지난 일정이라");

  // 보호자가 켜면 넣을 수 있다 — 지났다는 것은 켜 두지 않을 까닭이지 막을 까닭이 아니다.
  await togglePick(page, "가을 운동회");
  await expect(row(page, "가을 운동회")).not.toContainText("지난 일정이라 꺼 두었어요.");
  await expect(summary(page)).toContainText("가을 운동회");
  // 🚨 보호자가 다시 끄면 앱이 끈 것이 아니다 — "꺼 두었어요" 가 돌아오지 않는다.
  await togglePick(page, "가을 운동회");
  await expect(row(page, "가을 운동회")).not.toContainText("지난 일정이라 꺼 두었어요.");
  await togglePick(page, "가을 운동회");
  await page.getByRole("button", { name: "저장하고 캘린더에 6개 넣기" }).click();
  await page.waitForURL(`**/child/${CHILD}/home`);

  const titles = writes(calls, "POST", EVENTS).map(
    (c) => (c.body as { event: { title: string } }).event.title,
  );
  expect(titles).toContain("가을 운동회");
  expect(titles).toHaveLength(6);
});

test("🚨 버튼 바로 위에 무엇이 언제로 들어가는지 서고, 버튼이 개수를 말한다", async ({ page }) => {
  await page.goto(`/child/${CHILD}/home`);
  await page.getByRole("button", { name: "사진으로 적기" }).click();
  await page
    .getByRole("button", { name: /기기에 있는 최근 사진/ })
    .first()
    .click();
  await expect(page.getByText("알림장이나 식단표로 읽었어요.")).toBeVisible({ timeout: 15_000 });

  // 🚨 "잘 읽었어요" 를 펼치지 않은 채다 — 켜진 줄이 접혀 있어도 버튼 앞에서 무엇을 넣는지 보여야 한다.
  const card = summary(page);
  await expect(card).toContainText("도서관 나들이");
  await expect(card).toContainText(/독서 주간, .*부터 .*까지/);
  await expect(card).toContainText(/부모 참여 수업, .*오전 10:00부터 오후 12:00까지/);
  await expect(card).toContainText("누르면 일정 5개가 캘린더에 바로 들어가요.");

  const button = page.getByRole("button", { name: "저장하고 캘린더에 5개 넣기" });
  await expect(button).toBeVisible();
  // 🚨 넣을 일정이 있으면 이 버튼이 게이트 ㉠ 이다.
  expect(await gateMarks(page)).toContain("approve 버튼: 저장하고 캘린더에 5개 넣기");
});

test("다 끄면 그냥 저장이다 — 게이트 표식이 없다", async ({ page }) => {
  await openNotice(page);
  for (const title of [
    "도서관 나들이",
    "독서 주간",
    "부모 참여 수업",
    "원복 입고 등원",
    "물놀이",
  ]) {
    await togglePick(page, title);
  }
  await expect(summary(page)).toContainText(/캘린더\s*넣지 않아요/);
  await expect(page.getByRole("button", { name: "저장", exact: true })).toBeVisible();
  await expectNoGate(page);
});

test("버튼 하나로 켠 일정을 넣고, 받은 id 를 그 항목에 실어 저장한다", async ({ page }) => {
  const calls = recordApi(page);
  await openNotice(page);

  await page.getByRole("button", { name: "저장하고 캘린더에 5개 넣기" }).click();
  await page.waitForURL(`**/child/${CHILD}/home`);

  const events = writes(calls, "POST", EVENTS);
  expect(events.map((c) => (c.body as { event: { title: string } }).event.title)).toEqual([
    "도서관 나들이",
    "독서 주간",
    "부모 참여 수업",
    "원복 입고 등원",
    "물놀이",
  ]);
  // 🚨 읽은 시각과 읽은 기간이 그대로 들어간다 (#723).
  expect(events[0].body).toMatchObject({
    event: { starts_at: `${browserDaysAgo(-5)}T09:30:00+09:00`, ends_at: null, all_day: false },
    reminder: null,
  });
  expect(events[1].body).toMatchObject({
    event: {
      starts_at: `${browserDaysAgo(-8)}T00:00:00+09:00`,
      ends_at: `${browserDaysAgo(-12)}T23:59:00+09:00`,
      all_day: true,
    },
  });
  // 🚨 항목마다 키가 따로다 — 하나로 보내면 둘째부터 "같은 키 · 다른 본문" 이다.
  expect(new Set(events.map((c) => c.idempotencyKey)).size).toBe(5);

  // 🚨 순서는 일정 먼저, 그다음 commit 이다.
  const [commit] = writes(calls, "POST", COMMIT);
  expect(calls.indexOf(commit)).toBeGreaterThan(calls.indexOf(events[4]));
  const entries = (commit.body as { entries: Array<{ id: string; event_id: string | null }> })
    .entries;
  const linked = entries.filter((e) => e.event_id !== null);
  expect(linked.map((e) => e.id).sort()).toEqual(["pe_1", "pe_2", "pe_3", "pe_5", "pe_8"]);
  expect(new Set(linked.map((e) => e.event_id)).size).toBe(5);
  // 🚨 commit 은 캘린더에 쓰지 않는다.
  expect(commit.body).toMatchObject({ attach_to_calendar: false });
});

test("🚨 하나만 못 넣으면 저장하지 않고, 다시 누르면 못 넣은 것만 넣는다", async ({ page }) => {
  const calls = recordApi(page);
  await openNotice(page, "photo_event_partial");

  await page.getByRole("button", { name: "저장하고 캘린더에 5개 넣기" }).click();
  await expect(page.getByText(/일정 1개를 넣지 못해서 아직 저장하지 않았어요/)).toBeVisible();
  await expect(page.getByText("넣은 4개는 캘린더에 들어갔어요.", { exact: false })).toBeVisible();
  expect(writes(calls, "POST", COMMIT)).toHaveLength(0);

  // 넣은 줄은 칩으로 잠기고 체크가 없다 — 끈다고 캘린더에서 빠지지 않는다.
  await expect(row(page, "도서관 나들이")).toContainText("캘린더에 넣었어요");
  await expect(pick(page, "도서관 나들이")).toHaveCount(0);
  // 못 넣은 줄은 그 자리에서 까닭을 말한다.
  await expect(row(page, "독서 주간")).toContainText(
    "넣지 못했어요. 다시 누르면 이것만 다시 넣어요.",
  );
  await expect(summary(page)).toContainText(
    "캘린더에 넣은 일정 4개 말고는 아직 저장되지 않았어요.",
  );

  const failedKey = writes(calls, "POST", EVENTS)[1].idempotencyKey;
  await page.getByRole("button", { name: "저장하고 캘린더에 1개 넣기" }).click();
  await page.waitForURL(`**/child/${CHILD}/home`);

  const events = writes(calls, "POST", EVENTS);
  expect(events).toHaveLength(6);
  // 🚨 다시 보낸 것은 못 넣은 그 하나뿐이고, 같은 키다 — 응답만 못 받고 들어간 경우에도 두 번 안 들어간다.
  expect((events[5].body as { event: { title: string } }).event.title).toBe("독서 주간");
  expect(events[5].idempotencyKey).toBe(failedKey);

  const [commit] = writes(calls, "POST", COMMIT);
  const linked = (commit.body as { entries: Array<{ event_id: string | null }> }).entries.filter(
    (e) => e.event_id !== null,
  );
  expect(linked).toHaveLength(5);
});

test("🚨 넣는 도중에 화면을 떠나면 남은 일정은 보내지 않고 저장도 하지 않는다 (#828 리뷰)", async ({
  page,
}) => {
  const calls = recordApi(page);
  await openNotice(page);

  // 첫 일정이 나가는 순간 기기 뒤로가기로 떠난다. 목은 일정마다 왕복을 기다려서 다섯 건이 다 나가기 전이다.
  const first = page.waitForRequest((r) => r.method() === "POST" && EVENTS.test(r.url()));
  await page.getByRole("button", { name: "저장하고 캘린더에 5개 넣기" }).click();
  await first;
  await page.goBack();
  await page.waitForURL(`**/child/${CHILD}/home`);
  // 남은 넷이 나갔다면 다 나갔을 만큼 기다린다.
  await page.waitForTimeout(2000);

  // 🚨 보내고 있던 것은 되돌릴 수 없어 들어가지만, 그다음부터는 멈춘다.
  expect(writes(calls, "POST", EVENTS).length).toBeLessThan(5);
  expect(writes(calls, "POST", COMMIT)).toHaveLength(0);
});

test("🚨 일정을 넣고 저장만 실패하면 넣은 수를 말하고, 다시 누르면 저장만 보낸다", async ({
  page,
}) => {
  const calls = recordApi(page);
  await openNotice(page, "photo_commit_failed");

  await page.getByRole("button", { name: "저장하고 캘린더에 5개 넣기" }).click();
  await expect(
    page.getByText(/캘린더에 넣은 일정 5개는 들어갔어요\. 다시 누르면 읽은 내용만 저장해요/),
  ).toBeVisible();
  // 🚨 "아무것도 저장되지 않았어요" 는 이때 거짓이다.
  await expect(page.getByText("저장하지 못했어요. 아직 아무것도 저장되지 않았어요.")).toHaveCount(
    0,
  );
  expect(writes(calls, "POST", EVENTS)).toHaveLength(5);

  // 넣을 일정이 남지 않았으니 게이트가 아니라 그냥 저장이다.
  await page.getByRole("button", { name: "저장", exact: true }).click();
  await page.waitForURL(`**/child/${CHILD}/home`);

  // 🚨 다시 누를 때 일정을 다시 보내지 않는다.
  expect(writes(calls, "POST", EVENTS)).toHaveLength(5);
  const commits = writes(calls, "POST", COMMIT);
  expect(commits).toHaveLength(2);
  const linked = (
    commits[1].body as { entries: Array<{ event_id: string | null }> }
  ).entries.filter((e) => e.event_id !== null);
  expect(linked).toHaveLength(5);
});

test("읽은 결과가 사라져 저장이 404 면 버튼을 잠근다 — 다시 눌러도 같다", async ({ page }) => {
  await openNotice(page, "photo_commit_expired");

  await page.getByRole("button", { name: "저장하고 캘린더에 5개 넣기" }).click();
  await expect(
    page.getByText(/캘린더에 넣은 일정 5개는 들어갔어요\. .*사진을 다시 올려 주세요/),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "저장", exact: true })).toBeDisabled();
});

test("켜 둔 일정을 고쳐서 잠기면 꺼지고, 다시 풀려도 저절로 켜지지 않는다", async ({ page }) => {
  await openNotice(page);
  await expect(pick(page, "도서관 나들이")).toBeChecked();

  // 하루 종일을 끄고 시각을 비우면 잠긴다 — 시각 칸을 지울 길이 없어 날짜 대신 시각을 비운다.
  let sheet = await openSheet(page, "물놀이");
  await sheet.getByText("하루 종일", { exact: true }).click();
  await sheet.getByRole("button", { name: "이 내용으로 확인" }).click();
  await expect(pick(page, "물놀이")).toBeDisabled();
  await expect(pick(page, "물놀이")).not.toBeChecked();
  await expect(row(page, "물놀이")).toContainText("고치기에서 시작 시간을 고르면 넣을 수 있어요.");

  sheet = await openSheet(page, "물놀이");
  await sheet.getByText("하루 종일", { exact: true }).click();
  await sheet.getByRole("button", { name: "이 내용으로 확인" }).click();
  await expect(pick(page, "물놀이")).toBeEnabled();
  await expect(pick(page, "물놀이")).not.toBeChecked();
  await expect(page.getByRole("button", { name: "저장하고 캘린더에 4개 넣기" })).toBeVisible();
});

test("🚨 할 일은 시작하는 날을 고른 뒤에 켤 수 있고, 마감일이 아니라 그 날로 들어간다", async ({
  page,
}) => {
  const calls = recordApi(page);
  await openNotice(page);
  const sheet = await openSheet(page, "체험학습 동의서 회신");

  // 날짜 칸의 달력(닫힌 시트)도 같은 이름을 제목으로 들고 있어서 첫 번째(칸의 이름)만 본다.
  await expect(sheet.getByText("마감", { exact: true }).first()).toBeVisible();
  await expect(sheet.getByText("언제", { exact: true })).toHaveCount(0);
  await expect(sheet.getByText("시작하는 날", { exact: true }).first()).toBeVisible();

  // 시작하는 날로 이달 15일을 고른다 (달력이 이달로 열린다).
  await sheet.getByRole("button", { name: /시작하는 날/ }).click();
  const picker = page.getByRole("dialog", { name: "시작하는 날" });
  await picker.getByRole("gridcell").getByRole("button").filter({ hasText: /^15$/ }).click();
  await picker.getByRole("button", { name: "고르기" }).click();
  const start = `${browserDaysAgo(0).slice(0, 8)}15`;
  await sheet.getByRole("button", { name: "이 내용으로 확인" }).click();

  // 🚨 골라도 저절로 켜지지 않는다 — 넣을지는 보호자가 고른다.
  await expect(pick(page, "체험학습 동의서 회신")).toBeEnabled();
  await expect(pick(page, "체험학습 동의서 회신")).not.toBeChecked();
  await togglePick(page, "체험학습 동의서 회신");
  await page.getByRole("button", { name: "저장하고 캘린더에 6개 넣기" }).click();
  await page.waitForURL(`**/child/${CHILD}/home`);

  const todo = writes(calls, "POST", EVENTS).find(
    (c) => (c.body as { event: { title: string } }).event.title === "체험학습 동의서 회신",
  );
  expect(todo?.body).toMatchObject({
    event: { starts_at: `${start}T00:00:00+09:00`, ends_at: null, all_day: true },
  });
});

test("안내에는 캘린더에 넣을 때 칸이 없다", async ({ page }) => {
  await openNotice(page);
  const sheet = await openSheet(page, "비 오는 날 실내 활동");

  await expect(sheet.getByText("날짜가 없는 안내면 비워 두세요.")).toBeVisible();
  await expect(sheet.getByText("캘린더에 넣을 때")).toHaveCount(0);
});

test("여러 날 일정의 시작을 고치면 그날 하루로 줄었다고 말하고, 그 값으로 넣는다", async ({
  page,
}) => {
  const calls = recordApi(page);
  await openNotice(page);
  const sheet = await openSheet(page, "독서 주간");

  // 🚨 함께 넣는 끝을 시트에서 보여준다.
  await expect(sheet.getByText(/부터 .*까지 함께 넣어요/)).toBeVisible();

  // 하루 종일을 끄면 읽은 기간이 아니다 — 조용히 줄이지 않고 말한다.
  await sheet.getByText("하루 종일", { exact: true }).click();
  await sheet.getByRole("combobox", { name: "시작 시간, 시" }).click();
  await page.getByRole("option", { name: "오전 9시", exact: true }).click();
  await sheet.getByRole("combobox", { name: "시작 시간, 분" }).click();
  await page.getByRole("option", { name: "00분", exact: true }).click();
  await expect(sheet.getByText(/그날 하루로 넣어요/)).toBeVisible();
  await sheet.getByRole("button", { name: "이 내용으로 확인" }).click();

  // 버튼 위 목록이 고친 값을 말한다.
  await expect(summary(page)).toContainText(/독서 주간, .*오전 9:00/);
  await page.getByRole("button", { name: "저장하고 캘린더에 5개 넣기" }).click();
  await page.waitForURL(`**/child/${CHILD}/home`);

  const reading = writes(calls, "POST", EVENTS).find(
    (c) => (c.body as { event: { title: string } }).event.title === "독서 주간",
  );
  expect(reading?.body).toMatchObject({
    event: { starts_at: `${browserDaysAgo(-8)}T09:00:00+09:00`, ends_at: null, all_day: false },
  });
});

test("읽은 시각이 시각 칸에 채워져 열린다", async ({ page }) => {
  await openNotice(page);
  const sheet = await openSheet(page, "도서관 나들이");
  await expect(sheet.getByRole("combobox", { name: "시작 시간, 시" })).toHaveText(/오전 9시/);
  await expect(sheet.getByRole("combobox", { name: "시작 시간, 분" })).toHaveText(/30분/);
});

test("🚨 시트에서 고친 시작 시간이 목록의 날짜 줄과 저장되는 기록에도 들어간다", async ({
  page,
}) => {
  const calls = recordApi(page);
  await openNotice(page);
  await expect(row(page, "도서관 나들이")).toContainText("오전 9:30");

  // 오전으로 읽은 것을 저녁으로 고친다.
  const sheet = await openSheet(page, "도서관 나들이");
  await sheet.getByRole("combobox", { name: "시작 시간, 시" }).click();
  await page.getByRole("option", { name: "오후 8시", exact: true }).click();
  await sheet.getByRole("button", { name: "이 내용으로 확인" }).click();

  // 🚨 목록 줄이 고친 시각을 말한다 — 읽은 시각이 남으면 고친 것이 안 먹힌 것처럼 보인다.
  await expect(row(page, "도서관 나들이")).toContainText("오후 8:30");
  await expect(row(page, "도서관 나들이")).not.toContainText("오전 9:30");

  await page.getByRole("button", { name: "저장하고 캘린더에 5개 넣기" }).click();
  await page.waitForURL(`**/child/${CHILD}/home`);

  // 🚨 캘린더에 넣은 값과 저장하는 기록의 값이 같다. 한동안 기록에는 읽은 시각(9:30)이 남았다.
  const fixed = { starts_at: `${browserDaysAgo(-5)}T20:30:00+09:00`, all_day: false };
  const event = writes(calls, "POST", EVENTS).find(
    (c) => (c.body as { event: { title: string } }).event.title === "도서관 나들이",
  );
  expect(event?.body).toMatchObject({ event: fixed });
  const [commit] = writes(calls, "POST", COMMIT);
  const saved = (commit.body as { entries: Array<{ title: string }> }).entries.find(
    (e) => e.title === "도서관 나들이",
  );
  expect(saved).toMatchObject(fixed);
});

test("🚨 급식 날짜는 마감이 아니다 — 목록도 시트도 언제다 (#792 리뷰)", async ({ page }) => {
  await openNotice(page, "photo_meal_plan");

  const meal = page.getByRole("listitem").filter({ hasText: "급식" }).first();
  await expect(meal).toContainText("언제");
  await expect(meal).not.toContainText("마감");
  // 🚨 급식에는 캘린더에 넣기가 없다 — 식단표는 날짜가 있지만 일정이 아니다.
  await expect(meal.getByRole("checkbox")).toHaveCount(0);

  // 같은 메뉴가 여러 날 되풀이되어 제목으로는 한 줄을 못 집는다. 첫 급식 줄을 연다.
  await meal.getByRole("button", { name: "고치기" }).click();
  const sheet = page.getByRole("dialog");
  await expect(sheet.getByText("언제", { exact: true }).first()).toBeVisible();
  await expect(sheet.getByText("마감", { exact: true })).toHaveCount(0);
  await expect(sheet.getByText("캘린더에 넣을 때")).toHaveCount(0);
});

test("시각을 읽은 시작은 하루 종일 마감이 붙어도 시각과 끝을 그대로 쓴다 (#792 리뷰)", async ({
  page,
}) => {
  await openNotice(page);
  await expect(row(page, "부모 참여 수업")).toContainText("오전 10:00");

  const sheet = await openSheet(page, "부모 참여 수업");
  await expect(sheet.getByRole("combobox", { name: "시작 시간, 시" })).toHaveText(/오전 10시/);
  // 🚨 함께 넣는 끝을 시트에서 보여준다.
  await expect(sheet.getByText(/오후 12:00에 끝나요/)).toBeVisible();

  // 시작을 끝보다 늦게 고치면 끝이 빠지고, 빠졌다고 말한다.
  await sheet.getByRole("combobox", { name: "시작 시간, 시" }).click();
  await page.getByRole("option", { name: "오후 1시", exact: true }).click();
  await expect(
    sheet.getByText("시작 시간이나 하루 종일을 바꿔서, 읽어 둔 끝나는 시각은 빼고 넣어요."),
  ).toBeVisible();
  await expect(sheet.getByText(/에 끝나요/)).toHaveCount(0);
});

test("시각을 읽은 마감은 목록에 시각까지 보이고, 일정의 시작은 하루 종일에서 연다", async ({
  page,
}) => {
  await openNotice(page);
  await expect(row(page, "가정통신문 회신")).toContainText(/마감.*오후 6:00/);

  const sheet = await openSheet(page, "가정통신문 회신");
  // 🚨 마감의 하루 종일 여부는 일정의 시작과 상관없다. 마감 시각을 시작 시각으로 쓰지 않는다.
  await expect(sheet.getByRole("checkbox", { name: "하루 종일" })).toBeChecked();
  await expect(sheet.getByRole("group", { name: "시작 시간" })).toHaveCount(0);
});

test("딸린 정보(details)는 목록 줄에 이름과 값으로 서고, 없는 줄에는 아무것도 없다", async ({
  page,
}) => {
  const calls = recordApi(page);
  await openNotice(page);

  // 🚨 시트를 열지 않아도 보인다 — 일정(10시)과 다른 "9시까지 등원" 이 사라지면 안 된다.
  const lesson = row(page, "부모 참여 수업");
  await expect(lesson.getByRole("term").filter({ hasText: "등원 시각" })).toBeVisible();
  await expect(
    lesson.getByRole("definition").filter({ hasText: "당일은 9시까지 등원" }),
  ).toBeVisible();
  await expect(lesson.getByRole("term").filter({ hasText: "장소" })).toBeVisible();
  await expect(lesson.getByRole("definition").filter({ hasText: "강당" })).toBeVisible();
  await expect(row(page, "체험학습 동의서 회신")).toContainText("30,000원");

  // 🚨 없는 줄에는 빈 이름표도 빈 상자도 세우지 않는다 — 날짜 줄 하나뿐이다.
  await expect(row(page, "도서관 나들이").getByRole("term")).toHaveCount(1);

  // 🚨 긴 값은 줄을 바꾼다 — 줄이 카드 밖으로 넘쳐 가로 스크롤이 생기면 안 된다.
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  expect(overflow).toBe(0);

  // 🚨 고칠 수 없는 값이라 저장 본문에 싣지 않는다 — 서버가 보관한 값으로 저장한다.
  await page.getByRole("button", { name: /^저장하고 캘린더에/ }).click();
  await page.waitForURL(`**/child/${CHILD}/home`);
  const [commit] = writes(calls, "POST", COMMIT);
  const sent = (commit.body as { entries: Array<Record<string, unknown>> }).entries;
  expect(sent.length).toBeGreaterThan(0);
  for (const entry of sent) expect(entry).not.toHaveProperty("details");
});
