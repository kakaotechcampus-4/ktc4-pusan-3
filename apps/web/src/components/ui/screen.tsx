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
 * 🚨 **화면 아래에 붙는 것도 전부 이 컴포넌트가 소유한다** (`bottomBar` · `nav`).
 *    화면이 직접 `sticky` 를 붙이면 아래 여백을 0 으로 되돌려야 하는데, 그게 바로 위에서
 *    두 번 사고 난 그 조작이다. 둘 다 넘기면 **채팅바가 위, 네비가 아래**로 한 덩어리가 되고
 *    safe area 는 제일 아래 것이 받는다 — 각자 받으면 그만큼 두 번 밀린다.
 */
export function Screen({
  children,
  className,
  bottomBar,
  nav,
}: {
  children: ReactNode;
  className?: string;
  /** 스크롤과 무관하게 화면 아래에 붙는 영역 (03 홈의 채팅바). */
  bottomBar?: ReactNode;
  /** 그보다 더 아래에 붙는 화면 이동 바 (`ChildNav`). */
  nav?: ReactNode;
}) {
  const pinned = Boolean(bottomBar || nav);

  return (
    <main className="max-w-content pt-safe-8 mx-auto flex min-h-dvh w-full flex-col">
      {/* 좌우 여백이 본문에만 걸린다 — 아래 바의 구분선은 화면 끝까지 가야 한다. */}
      <div
        className={cn(
          "flex flex-1 flex-col px-3 min-[380px]:px-4",
          // 붙는 것이 없으면 이 컴포넌트가 아래 여백까지 소유한다 (기존 동작).
          pinned ? "pb-4" : "pb-safe-8",
          className,
        )}
      >
        {children}
      </div>

      {pinned ? (
        // sticky 라 본문이 짧으면 그냥 아래에 놓이고, 길면 스크롤 위에 떠 있는다.
        // 🚨 pb-safe-* 는 safe area + 여백을 한 속성에 합친다 — py-* 를 겹치지 않는다.
        <div className="bg-canvas sticky bottom-0">
          {bottomBar ? (
            // 🚨 위 여백보다 아래 여백이 커야 한다 (8 / 20). 채팅바와 네비 **사이**가
            //    채팅바 **안**(제안 줄↔알약 8px)보다 넓어야 두 덩어리로 갈린다 —
            //    예전엔 12/12 라 사이와 안이 같았고, 그래서 선을 아무리 그어도
            //    하단 전체가 줄 쳐진 슬래브 하나로 읽혔다.
            <div
              className={cn(
                "border-line border-t px-3 pt-2 min-[380px]:px-4",
                nav ? "pb-5" : "pb-safe-4",
              )}
            >
              {bottomBar}
            </div>
          ) : null}
          {/* 🚨 네비는 **바닥 면**이다. 선이 아니라 색으로 갈린다 — `line` 1px 은 canvas 위에서
              1.21:1 이라 같은 선이 위(고정 영역 시작)에도 있으면 두 뜻이 한 신호를 나눠 쓴다.
              `surface-muted` 는 대비로는 1.07:1 이지만 **바 전체 면적**에 걸려서 훨씬 세다. */}
          {nav ? <div className="bg-surface-muted pb-safe-2">{nav}</div> : null}
        </div>
      ) : null}
    </main>
  );
}
