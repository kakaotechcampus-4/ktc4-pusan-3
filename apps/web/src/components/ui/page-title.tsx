import type { ReactNode } from "react";

import { cn } from "@/lib/cn";

/**
 * 화면 제목. 디자인 시스템 §4 의 `display` 다.
 *
 * 🚨 §9 가 **좁은 폰(< 380px)에서는 `display` 를 `title` 로 낮추라**고 정했다.
 *    화면마다 손으로 쓰면 어긋나므로 여기 한 곳에 둔다 — 24px 그대로면 390 폭에서 한 줄인
 *    제목이 360px 기기에서 두 줄로 흐른다.
 * 🚨 **제목을 `<br />` 로 끊지 않는다** (디자인 시스템 §4). 넘치면 `keep-all` 이 어절에서 끊는다.
 */
export function PageTitle({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <h1 className={cn("text-title min-[380px]:text-display text-ink", className)}>{children}</h1>
  );
}
