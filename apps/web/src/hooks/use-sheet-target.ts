"use client";

import { useState } from "react";

/**
 * 고치는 대상 하나를 들고 여닫는 시트의 상태 (#490).
 *
 * 🚨 **`{editing ? <Sheet open /> : null}` 으로 그리지 않는다.** 닫는 순간 시트가 통째로 빠져서
 *    내려가는 움직임 없이 사라진다 — 열 때는 올라오는데 닫을 때만 툭 끊긴다. 대상은 닫힌 뒤에도
 *    들고 있고, 여닫는 것은 `open` 이 한다.
 * 🚨 **열 때마다 `session` 이 바뀐다.** 시트 폼이 `useState` 로 값을 들고 있어서, 같은 대상을 다시
 *    열 때 인스턴스를 재사용하면 지난번에 고치다 만 값이 남는다. 그래서 `key={session}` 으로 연다.
 *    닫을 때는 바꾸지 않는다 — 바꾸면 그 자리에서 새로 만들어져 움직임이 끊긴다.
 */
export function useSheetTarget<T>() {
  const [state, setState] = useState<{ target: T; open: boolean; session: number } | null>(null);

  return {
    /** 마지막으로 연 대상. 🚨 닫힌 뒤에도 남는다 — 열렸는지는 `open` 으로 본다. */
    target: state?.target ?? null,
    open: state?.open ?? false,
    session: state?.session ?? 0,
    show: (target: T) =>
      setState((prev) => ({ target, open: true, session: (prev?.session ?? 0) + 1 })),
    close: () => setState((prev) => (prev ? { ...prev, open: false } : prev)),
  };
}
