/**
 * Android 물리 뒤로가기 — 웹뷰 히스토리로 갈지, 앱을 닫을지 (#462).
 *
 * 하단 네비의 네 칸(홈 · 캘린더 · 기록/기억 · 아이)은 서로 오가는 "제자리" 라 그 사이를 뒤로 되짚지 않는다.
 * 탭 이동은 웹에서 push 로 쌓이고 로그인 화면도 히스토리에 남아 있어서, 히스토리만 보면 앱이 닫힐 차례가
 * 오지 않았다. 그래서 **상위 탭에서는 히스토리가 있어도 닫는 쪽**으로 간다.
 *
 * 🚨 **닫기 전에 한 번 묻는다.** 앱이 닫힐 차례의 첫 누름은 토스트만 띄우고, `EXIT_WINDOW_MS` 안에
 *    한 번 더 누르면 닫는다. 상위 탭이 아니어도 더 갈 히스토리가 없으면 같은 규칙이다 — 닫히는 길은 하나다.
 *
 * 🚨 **셸이 웹의 화면 경로를 안다.** 셸이 아는 웹 경로는 로그인 복귀(`/auth/callback` · `auth-session.ts`)와
 *    API 경로(`/api` · `internal-url.ts`)가 있었는데, 그 둘은 흐름의 입구다. 이건 **화면**이라 "화면은 웹에 있다"
 *    (CLAUDE.md §1, `safe-area.ts` 머리말)에서 한 걸음 더 나간다. 그래도 경로를 보는 이유는, 웹이 셸에 "여기가
 *    상위다" 를 알리려면 `onMessage` 를 열어야 해서다(§4 금지). 셸이 아는 것은 **어느 화면이 상위인가** 하나뿐이고
 *    그 화면을 그리거나 칠하지는 않는다. `apps/web/src/components/child-nav.tsx` 의 `TABS` 가 바뀌면 여기를 같이 고친다.
 */

/** 하단 네비 네 칸 — `child-nav.tsx` 의 `TABS[].path` 와 같은 값. */
const ROOT_PATH = /^\/child\/[^/]+\/(home|calendar|memories|profile)\/?$/;

/** 두 번째 누름을 기다리는 시간. Android 토스트 `SHORT` 와 비슷하게 맞춘다. */
export const EXIT_WINDOW_MS = 2000;

export const EXIT_HINT = "'뒤로가기' 버튼을 한 번 더 누르면 종료됩니다.";

/** 하단 네비 네 칸 중 하나인가. 쿼리 · 해시는 보지 않는다. */
export function isRootUrlOf(url: string, allowedOrigin: string): boolean {
  let parsed: URL;
  try {
    parsed = new URL(url);
  } catch {
    return false;
  }
  if (parsed.origin !== allowedOrigin) return false;
  return ROOT_PATH.test(parsed.pathname);
}

export type BackAction = "go-back" | "confirm-exit" | "exit";

/**
 * 이번 누름에 할 일.
 *
 * - `go-back` — 웹뷰 히스토리로 한 칸
 * - `confirm-exit` — 토스트만 띄운다. 호출한 쪽이 지금 시각을 `lastPressAt` 으로 기억한다
 * - `exit` — 앱을 닫는다 (OS 기본 동작에 넘긴다)
 */
export function decideBack(input: {
  atRoot: boolean;
  canGoBack: boolean;
  now: number;
  /** 직전 `confirm-exit` 시각. 없으면 null. */
  lastPressAt: number | null;
}): BackAction {
  if (!input.atRoot && input.canGoBack) return "go-back";
  const confirmed = input.lastPressAt != null && input.now - input.lastPressAt < EXIT_WINDOW_MS;
  return confirmed ? "exit" : "confirm-exit";
}
