import type { ReactNode } from "react";

import { cn } from "@/lib/cn";

/**
 * 화면 컨테이너. 디자인 시스템 §5 · §9 를 한 곳에 고정한다.
 *
 * - 최대 폭 560px · 가운데 정렬. 그 이상에서 2열로 벌리지 않는다 (문서 §9)
 * - 좌우 여백 16px, 380px 미만 기기에서만 12px
 * - pt-safe / pb-safe — 웹뷰 안에서는 셸이 이미 먹어서 0 이 되고,
 *   모바일 브라우저로 직접 들어왔을 때만 값이 생긴다
 */
export function Screen({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <main
      className={cn(
        "pt-safe pb-safe max-w-content mx-auto flex min-h-dvh w-full flex-col px-3 min-[380px]:px-4",
        className,
      )}
    >
      {children}
    </main>
  );
}
