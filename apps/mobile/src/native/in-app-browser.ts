/**
 * 앱 밖 링크를 **앱 위에 얹어** 연다 — Android = Custom Tabs, iOS = `SFSafariViewController`.
 *
 * 약관 "전문 보기" 를 시스템 브라우저로 넘기면 앱을 떠나 Chrome 이 앞에 오고, 다 읽은 사람은 최근 앱
 * 화면에서 앱을 다시 찾아야 한다. 가입 동의는 **필수 동의 직전에 읽으라고 둔 링크**라, 돌아오는 길이
 * 번거로울수록 안 읽고 체크하게 된다 (#212). in-app 브라우저는 X 하나로 같은 화면에 돌아온다.
 *
 * 🚨 **웹뷰 안에서 열지 않는 규칙은 그대로다** (CLAUDE.md §4). in-app 브라우저는 진짜 브라우저라
 *    도메인 · 자물쇠가 보이고, 앱이 그 안을 들여다볼 수 없다. 웹뷰는 둘 다 아니다.
 */

/**
 * in-app 브라우저로 열 수 있는 주소인가 — `http(s)` 만.
 *
 * `tel:` · `mailto:` · `intent:` · 다른 앱 스킴은 Custom Tabs 가 열지 못한다. 그런 것은 지금처럼
 * `Linking` 에 맡겨 OS 가 알맞은 앱을 고르게 한다.
 */
export function isWebUrl(url: string): boolean {
  try {
    const { protocol } = new URL(url);
    return protocol === "https:" || protocol === "http:";
  } catch {
    return false;
  }
}
