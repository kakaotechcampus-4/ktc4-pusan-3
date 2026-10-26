import type { ReactNode } from "react";

import { cn } from "@/lib/cn";

/**
 * 디자인 시스템 §7 `skeleton`. 모양이 글자가 아닌 자리(그래프 · 사진)에 쓴다 — 글자 자리는 `SkeletonText`.
 *
 * 🚨 `prefers-reduced-motion` 에서는 **정지한다** (문서 §8). 전역 CSS 가 애니메이션을 끄므로
 *    여기서 따로 막을 것은 없고, 멈춰도 "자리를 잡아 둔 블록" 으로 읽히도록 색만 남는다.
 *    스피너와 달리 숨기지 않는다 — 숨기면 레이아웃이 두 번 흔들린다.
 *
 * 화면 전체를 스켈레톤으로 덮지 않는다. 값이 들어올 자리에만 놓아서, 데이터가 오면
 * 같은 자리에 글자가 들어온다.
 *
 * 🚨 **스켈레톤은 들어올 내용과 같은 상자를 차지해야 한다** (CLS). 데이터가 오는 순간 높이가
 *    달라지면 아래의 모든 것이 밀린다 — 10 설정은 3줄짜리 블록이 보호자 카드 세 장으로 바뀌며
 *    CLS 0.2 가 나왔다. 그래서 줄은 `SkeletonText` 로 **그 글자 크기의 줄 높이**를 그대로 차지하고,
 *    목록은 진짜 줄과 같은 컨테이너 · 여백에 글자 대신 이것을 넣어 만든다 (`SettingsRowSkeleton` 처럼
 *    진짜 컴포넌트 옆에 둔다 — 한쪽만 고치면 어긋나는 것이 보이게).
 */
export function Skeleton({ className }: { className: string }) {
  // 🚨 크기를 기본값으로 깔지 않는다 — `cn()` 은 tailwind-merge 가 아니라서 `h-4` 위에 `h-40` 을 얹으면
  //    어느 쪽이 이길지가 생성된 CSS 순서에 달린다. 크기는 부르는 쪽이 `className` 으로 정한다.
  return (
    <span aria-hidden className={cn("bg-line rounded-field animate-skeleton block", className)} />
  );
}

/** 글자 크기 토큰 (globals.css `@theme --text-*`). 줄 높이가 토큰마다 달라서 크기로 고른다. */
export type SkeletonTextSize =
  "caption" | "label" | "body-sm" | "body" | "section" | "title" | "display";

const TEXT: Record<SkeletonTextSize, string> = {
  caption: "text-caption",
  label: "text-label",
  "body-sm": "text-body-sm",
  body: "text-body",
  section: "text-section",
  title: "text-title",
  display: "text-display",
};

const WIDTH = {
  full: "w-full",
  "3/4": "w-3/4",
  "2/3": "w-2/3",
  "1/2": "w-1/2",
  "1/3": "w-1/3",
  "1/4": "w-1/4",
  /** 숫자 하나 · "0건" 같은 짧은 값 */
  short: "w-8",
} as const;

export type SkeletonWidth = keyof typeof WIDTH;

/**
 * 글자 한 줄 자리.
 *
 * 바깥 상자가 그 글자 크기의 **줄 높이**를 그대로 가진다 (글자가 들어와도 높이가 같다).
 * 안의 막대는 글자 몸통만큼만 칠한다 — 줄 높이 전체를 칠하면 줄끼리 붙어 한 덩어리로 보인다.
 * 바깥 여백(`mt-*`)은 진짜 글자에 준 것을 `className` 으로 그대로 넘긴다.
 */
export function SkeletonText({
  size = "body",
  width = "full",
  className,
}: {
  size?: SkeletonTextSize;
  width?: SkeletonWidth;
  className?: string;
}) {
  return (
    <span aria-hidden className={cn("block", TEXT[size], className)}>
      <span
        className={cn(
          "bg-line rounded-field animate-skeleton inline-block h-[0.85em] max-w-full align-middle",
          WIDTH[width],
        )}
      />
    </span>
  );
}

/** `IconTile` 자리 (36px · `rounded-field`). */
export function SkeletonTile() {
  return (
    <span aria-hidden className="bg-line rounded-field animate-skeleton block size-9 shrink-0" />
  );
}

/**
 * 스켈레톤 한 덩어리. 스크린리더에는 "불러오는 중" 을 **한 번만** 알린다 — 안의 조각은 전부 `aria-hidden` 이다.
 * `className` 은 진짜 내용을 감싸는 상자의 배치(`flex flex-col gap-*`)를 그대로 넘긴다.
 */
export function SkeletonGroup({
  label = "불러오는 중",
  className,
  children,
}: {
  label?: string;
  className?: string;
  children: ReactNode;
}) {
  return (
    <div role="status" aria-label={label} className={className}>
      {children}
    </div>
  );
}

/**
 * 모양을 정하기 어려운 영역 하나 (본문 세 줄).
 * 🚨 **들어올 내용의 모양을 알면 쓰지 않는다** — 위 `SkeletonText` 로 그 모양을 만든다.
 */
export function SkeletonBlock({ label = "불러오는 중" }: { label?: string }) {
  return (
    <SkeletonGroup label={label}>
      <SkeletonText width="1/3" />
      <SkeletonText />
      <SkeletonText width="2/3" />
    </SkeletonGroup>
  );
}
