import type { ButtonHTMLAttributes, ReactNode } from "react";

import { cn } from "@/lib/cn";

/**
 * 디자인 시스템 §7 버튼 표를 그대로 옮겼다. 값을 바꾸려면 문서를 먼저 고친다.
 *
 * 🚨 한 화면에 primary 는 하나다. 두 개면 무엇이 다음 행동인지 부모가 판단해야 한다.
 * 🚨 approve 는 승인 게이트 2곳 전용(52px · 전체 폭), danger 는 파괴적 확정 전용이다.
 *    거절·취소는 danger 가 아니라 secondary 다 — 거절은 파괴가 아니다.
 */
export type ButtonVariant = "primary" | "secondary" | "tertiary" | "approve" | "danger" | "kakao";

/** 비활성은 브랜드색을 흐리게 만들지 않는다 — "눌릴 것처럼 보이는데 안 눌리는" 상태가 제일 나쁘다 (문서 §2-5). */
const DISABLED =
  "disabled:bg-surface-muted disabled:text-ink-subtle disabled:border disabled:border-line disabled:shadow-none";

/**
 * 🚨 `hover:` 는 `globals.css` 에서 `@media (hover: hover)` 로 덮어 뒀다 — Tailwind 기본값은
 *    그냥 `:hover` 라서 터치 기기에서 탭 뒤에 **눌러붙는다.** 호버는 브라우저로 들어온
 *    경우를 위한 것이고, 웹뷰에는 호버가 없다 (문서 §8).
 *    누른 느낌(`active:`)은 터치에서도 걸리므로 모든 변형에 넣는다.
 */
const VARIANT: Record<ButtonVariant, string> = {
  primary: "h-12 px-5 rounded-field bg-brand text-white hover:bg-brand-hover active:bg-brand-hover",
  secondary:
    "h-12 px-5 rounded-field bg-surface text-ink border border-line-strong hover:bg-surface-muted active:bg-surface-muted",
  tertiary: "h-11 px-2 rounded-field text-brand hover:bg-brand-soft active:bg-brand-soft",
  approve:
    "h-13 w-full rounded-field bg-brand text-white hover:bg-brand-hover active:bg-brand-hover",
  danger:
    "h-12 px-5 rounded-field bg-danger text-white hover:bg-danger-hover active:bg-danger-hover",
  // 외부 브랜드. 카카오가 정한 색·모양을 따라야 해서 예외로 둔 변형이다 (문서 §2-6).
  kakao:
    "h-12 px-5 rounded-field bg-kakao text-kakao-ink hover:bg-kakao-hover active:bg-kakao-hover",
};

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  /** 전체 폭으로 늘린다. approve 는 항상 전체 폭이라 이 값을 보지 않는다. */
  block?: boolean;
  children: ReactNode;
}

export function Button({
  variant = "primary",
  block = false,
  className,
  type = "button",
  children,
  ...props
}: ButtonProps) {
  return (
    <button
      type={type}
      className={cn(
        "text-button ease-standard inline-flex items-center justify-center gap-2 transition-colors duration-120",
        VARIANT[variant],
        block && variant !== "approve" && "w-full",
        DISABLED,
        className,
      )}
      {...props}
    >
      {children}
    </button>
  );
}
