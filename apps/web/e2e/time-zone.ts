/**
 * 테스트 브라우저의 시간대. `playwright.config.ts` 의 `timezoneId` 와 테스트 코드가 같이 쓴다.
 *
 * 🚨 **테스트 코드(Node)에서 날짜를 만들 때 이 시간대로 만든다.** 목은 브라우저 안에서 돌아서
 *    "어제" 를 브라우저 시간대로 만드는데, Node 는 러너 시간대(CI 는 UTC)다. 한국 시간 0시~9시에는
 *    둘의 날짜가 하루 어긋나서, 그 시각에 도는 CI 만 실패했다 (#354 PR 의 e2e).
 */
export const BROWSER_TIME_ZONE = "Asia/Seoul";

const DAY_MS = 24 * 60 * 60 * 1000;

const DATE_KEY = new Intl.DateTimeFormat("en-CA", {
  timeZone: BROWSER_TIME_ZONE,
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
});

/** 브라우저 시간대로 n 일 전의 `YYYY-MM-DD`. 목의 `daysAgo` 와 같은 날을 가리킨다. */
export function browserDaysAgo(n: number): string {
  return DATE_KEY.format(new Date(Date.now() - n * DAY_MS));
}
