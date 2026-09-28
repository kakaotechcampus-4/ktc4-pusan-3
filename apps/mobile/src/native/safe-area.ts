/**
 * safe area — **셸이 칠하지 않고 값만 넘긴다.**
 *
 * 예전에는 `SafeAreaView` 가 상태바 · 제스처바 자리를 자기 배경색으로 칠하고 웹뷰를 안쪽으로
 * 밀었다. 간격은 맞았지만 **색이 어긋났다** — 페이지 바탕은 `canvas`, 하단 네비가 서는 면은
 * `surface-muted`, 시트가 열리면 딤이라 화면마다 다른데 셸은 한 가지 색밖에 모른다.
 * 그래서 위아래에 흰 띠가 남았다 (#133).
 *
 * 🚨 **그렇다고 셸이 화면별로 색을 바꿔 칠하면 안 된다.** 그러려면 셸이 웹 화면을 알아야 하고,
 *    그건 "화면은 전부 웹에 있다"(CLAUDE.md §1)를 깨는 길이다. 대신 **자리만 비워 주고 값을 넘겨**
 *    페이지가 자기 색으로 끝까지 칠하게 한다.
 *
 * 🚨 **웹이 스스로 알 수 없어서 셸이 넘긴다.** Android 웹뷰의 `env(safe-area-inset-*)` 는
 *    디스플레이 컷아웃만 세고 **상태바 · 제스처바는 세지 않는다.** 그래서 웹뷰 안에서는 늘 0 이다.
 *    그 값을 아는 것은 네이티브뿐이다 — 정확히 "네이티브만 할 수 있는 것"(§1)이다.
 *
 * 받는 쪽은 `apps/web/src/app/globals.css` 의 `pt-safe-*` · `pb-safe-*` 다.
 * 이름을 바꾸려면 **양쪽을 같이** 고친다.
 */

import type { EdgeInsets } from "react-native-safe-area-context";

/** 웹이 보는 이름. `globals.css` 와 같은 문자열이어야 한다. */
const TOP = "--shell-inset-top";
const BOTTOM = "--shell-inset-bottom";

/**
 * 웹뷰가 문서를 읽기 **전에** 꽂는 받침대.
 *
 * 🚨 값을 여기서 바로 넣지 않고 함수를 먼저 깔아 두는 이유는 **타이밍** 이다. 이 스크립트가
 *    도는 시점에 `document.documentElement` 가 아직 없을 수 있어서, 없으면 `DOMContentLoaded`
 *    까지 미뤘다가 넣는다. 첫 페인트는 그보다 한참 뒤(React 렌더)라 깜빡이지 않는다.
 */
export const SAFE_AREA_SCRIPT = `(function () {
  if (window.__icatchSetInsets) return;

  function paint(top, bottom) {
    var root = document.documentElement;
    if (!root) return false;
    root.style.setProperty(${JSON.stringify(TOP)}, top);
    root.style.setProperty(${JSON.stringify(BOTTOM)}, bottom);
    return true;
  }

  window.__icatchSetInsets = function (top, bottom) {
    if (paint(top, bottom)) return;
    document.addEventListener('DOMContentLoaded', function () { paint(top, bottom); });
  };
})();
true;`;

/** 지금 값을 웹에 넣는 스크립트. 첫 주입에도, 값이 바뀔 때도 같은 것을 쓴다. */
export function safeAreaScript(insets: EdgeInsets): string {
  // 🚨 `Math.round` — 소수점이 섞이면 기기마다 1px 이 들쭉날쭉해 보인다.
  const top = `${Math.round(insets.top)}px`;
  const bottom = `${Math.round(insets.bottom)}px`;
  return `window.__icatchSetInsets(${JSON.stringify(top)},${JSON.stringify(bottom)});true;`;
}
