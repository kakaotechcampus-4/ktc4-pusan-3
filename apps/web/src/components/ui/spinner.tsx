import { cn } from "@/lib/cn";

/**
 * 기다리는 중이라는 표시. 버튼 안이나 대기 화면에 쓴다.
 *
 * 🚨 `prefers-reduced-motion` 에서는 **숨긴다** (문서 §8). 전역 CSS 가 애니메이션을 멈추는데,
 *    멈춘 스피너는 "고장난 화면" 으로 읽힌다 — 옆의 문구("저장하는 중…")가 상태를 대신 말한다.
 *
 * 회전은 1초에 1바퀴다. 1초에 3회 이상 깜빡이는 요소를 만들지 않는다는 규칙 안에 있다.
 */
export function Spinner({ className }: { className?: string }) {
  return (
    <span
      aria-hidden
      className={cn(
        "size-4 shrink-0 animate-spin rounded-full border-2 border-current border-t-transparent motion-reduce:hidden",
        className,
      )}
    />
  );
}
