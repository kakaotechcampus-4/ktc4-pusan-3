import { cn } from "@/lib/cn";

/**
 * 디자인 시스템 §7 `skeleton`.
 *
 * 🚨 `prefers-reduced-motion` 에서는 **정지한다** (문서 §8). 전역 CSS 가 애니메이션을 끄므로
 *    여기서 따로 막을 것은 없고, 멈춰도 "자리를 잡아 둔 블록" 으로 읽히도록 색만 남는다.
 *    스피너와 달리 숨기지 않는다 — 숨기면 레이아웃이 두 번 흔들린다.
 *
 * 화면 전체를 스켈레톤으로 덮지 않는다. 값이 들어올 자리에만 놓아서, 데이터가 오면
 * 같은 자리에 글자가 들어온다.
 */
export function Skeleton({ className }: { className?: string }) {
  return (
    <span
      aria-hidden
      className={cn("bg-line rounded-field animate-skeleton block h-4 w-full", className)}
    />
  );
}

/** 로딩 중인 영역 하나. 스크린리더에는 "불러오는 중" 한 번만 알린다. */
export function SkeletonBlock({ label = "불러오는 중" }: { label?: string }) {
  return (
    <div role="status" aria-label={label} className="flex flex-col gap-2">
      <Skeleton className="w-1/3" />
      <Skeleton />
      <Skeleton className="w-2/3" />
    </div>
  );
}
