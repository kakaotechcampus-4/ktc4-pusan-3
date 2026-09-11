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
 *
 * 🚨 **하단 고정 바도 이 컴포넌트가 소유한다** (`bottomBar`). 화면이 직접 `sticky` 를 붙이면
 *    아래 여백을 0 으로 되돌려야 하는데, 그게 바로 위에서 두 번 사고 난 그 조작이다.
 *    바를 넘기면 본문의 아래 여백을 바가 가져가고, safe area 도 바가 받는다.
 */
export function Screen({
  children,
  className,
  bottomBar,
}: {
  children: ReactNode;
  className?: string;
  /** 스크롤과 무관하게 화면 아래에 붙는 영역 (03 홈의 채팅바). 좌우·아래 여백은 여기서 준다. */
  bottomBar?: ReactNode;
}) {
  return (
    <main className="max-w-content pt-safe-8 mx-auto flex min-h-dvh w-full flex-col">
      {/* 좌우 여백이 본문에만 걸린다 — 하단 바의 구분선은 화면 끝까지 가야 한다. */}
      <div
        className={cn(
          "flex flex-1 flex-col px-3 min-[380px]:px-4",
          // 바가 없으면 이 컴포넌트가 아래 여백까지 소유한다 (기존 동작).
          bottomBar ? "pb-4" : "pb-safe-8",
          className,
        )}
      >
        {children}
      </div>

      {bottomBar ? (
        // sticky 라 본문이 짧으면 그냥 아래에 놓이고, 길면 스크롤 위에 떠 있는다.
        // 🚨 pb-safe-4 는 safe area + 16px 을 한 속성에 합친다 — py-* 를 겹치지 않는다.
        <div className="bg-canvas border-line pb-safe-4 sticky bottom-0 border-t px-3 pt-3 min-[380px]:px-4">
          {bottomBar}
        </div>
      ) : null}
    </main>
  );
}
