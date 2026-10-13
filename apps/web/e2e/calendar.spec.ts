import { CHILD, expect, test } from "./fixtures";
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
});

test("내 일기는 일기 구역에 그대로 선다", async ({ page }) => {
  await page.goto(`${CALENDAR}?date=${browserDaysAgo(1)}`);

  await expect(page.getByText("저녁에 블록을 한참 쌓다가 무너지니까 웃었다.")).toBeVisible();
  await expect(page.getByRole("button", { name: "일기 고치기" })).toBeVisible();
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
