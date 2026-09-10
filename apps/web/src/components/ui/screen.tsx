import type { ReactNode } from "react";

import { cn } from "@/lib/cn";

/**
 * 화면 컨테이너. 디자인 시스템 §5 · §9 를 한 곳에 고정한다.
 *
 * - 최대 폭 560px · 가운데 정렬. 그 이상에서 2열로 벌리지 않는다 (문서 §9)
 * - 좌우 여백 16px, 380px 미만 기기에서만 12px
 * - 상하 여백 32px(간격 토큰 `2xl`) + 노치·홈 인디케이터 (문서 §5 "여백 철학")
 *
 * 🚨 **상하 여백은 이 컴포넌트가 소유한다. `className` 으로 `py-*` 를 넘기지 않는다.**
 *    safe-area 유틸과 `py-*` 는 같은 `padding-top`/`padding-bottom` 속성이라
 *    뒤에 오는 쪽이 이긴다. 실제로 화면 5개가 `py-8` 을 넘기고 있었는데 전부 죽어서
 *    **상하 여백이 0** 이었다 (하단 문구가 화면 맨 아래 모서리에 붙었다).
 *    그래서 두 값을 calc 로 한 속성에 합쳐 둔다.
 */
export function Screen({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <main
      className={cn(
        "max-w-content mx-auto flex min-h-dvh w-full flex-col px-3 min-[380px]:px-4",
        // 32px = 간격 토큰 2xl. 웹뷰에서는 셸이 safe area 를 이미 먹어서 0 이 되고,
        // 모바일 브라우저로 직접 들어왔을 때만 그만큼 더 붙는다.
        "pt-safe-8 pb-safe-8",
        className,
      )}
    >
      {children}
    </main>
  );
}
