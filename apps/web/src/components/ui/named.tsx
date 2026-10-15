import { ViewTransition, type ReactNode } from "react";

/**
 * 이름 붙인 전환 한 쌍의 한쪽 (`lib/view-transition.ts` 의 `VIEW_TRANSITION`). 🚨 `default="none"` 과
 * `share` 를 **같이** 준다 — 이름 붙인 것이 관계없는 전환(다른 화면으로 가는 이동)마다 제 혼자 페이드하지
 * 않게 막고, 쌍이 만나면 이어 붙인다. `share` 가 빠지면 쌍이 조용히 안 이어진다.
 */
export function Named({ name, children }: { name: string; children: ReactNode }) {
  return (
    <ViewTransition name={name} share="auto" default="none">
      {children}
    </ViewTransition>
  );
}
