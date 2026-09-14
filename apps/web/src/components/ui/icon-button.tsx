import type { ButtonHTMLAttributes, ReactNode } from "react";

import { cn } from "@/lib/cn";

/**
 * 디자인 시스템 §7 아이콘 버튼 — **글자 없이 아이콘만** 있는 원형 버튼.
 *
 * 🚨 `label` 이 필수다. 글자가 없으니 스크린리더에는 이것밖에 남지 않는다 —
 *    라벨 없는 아이콘 버튼은 만들지 않는다 (문서 §10).
 *
 * 🚨 높이만 `min-h-*` 규칙에서 빠진다. 글자가 들어가지 않아 길어질 일이 없고, 정사각형이
 *    무너지면 원이 타원이 된다 — 문서 §13 이 허용한 `h-touch aspect-square` 조합이다.
 *
 * `ghost` 의 hover/press 가 `brand-soft` 인 것은 프로토타입 그대로다. 채팅바처럼 버튼이
 * 여러 개 붙어 있는 자리에서 뉴트럴 tint 는 눌렸는지가 안 보였다.
 */
export type IconButtonTone = "ghost" | "brand";

const TONE: Record<IconButtonTone, string> = {
  ghost:
    "text-ink-muted hover:bg-brand-soft hover:text-brand-ink active:bg-brand-soft active:text-brand-ink disabled:bg-transparent disabled:text-ink-subtle",
  // 🚨 비활성 배경이 `surface-muted` 가 아니라 `surface` 다. 이 버튼이 앉는 채팅바가
  //    `surface-muted` 라, 같은 색이면 **버튼이 통째로 사라진다** (실제로 안 보였다).
  brand:
    "bg-brand text-white hover:bg-brand-hover active:bg-brand-hover disabled:bg-surface disabled:text-ink-subtle disabled:border disabled:border-line",
};

interface IconButtonProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, "children"> {
  /** 스크린리더가 읽을 이름. 눈에 보이지 않으므로 생략할 수 없다. */
  label: string;
  tone?: IconButtonTone;
  children: ReactNode;
}

export function IconButton({
  label,
  tone = "ghost",
  className,
  type = "button",
  children,
  ...props
}: IconButtonProps) {
  return (
    <button
      type={type}
      aria-label={label}
      title={label}
      className={cn(
        "h-touch ease-standard flex aspect-square shrink-0 items-center justify-center rounded-full transition-colors duration-120",
        TONE[tone],
        className,
      )}
      {...props}
    >
      {children}
    </button>
  );
}
