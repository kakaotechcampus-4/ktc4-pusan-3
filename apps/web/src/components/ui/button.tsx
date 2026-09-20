import type { ButtonHTMLAttributes, ReactNode } from "react";

import { cn } from "@/lib/cn";

/**
 * 디자인 시스템 §7 버튼. 높이는 `globals.css` 의 `--height-*` 토큰이 원본이고,
 * 좌우 여백처럼 이 컴포넌트에서만 쓰는 치수는 이 파일이 원본이다.
 *
 * 🚨 높이는 `min-h-*` 다. 문구가 두 줄이 되거나 글자를 키우면 버튼이 늘어나야 한다 —
 *    고정 높이면 글자가 버튼 밖으로 흘러 무엇을 누르는지가 가려진다 (문서 §10).
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
  primary: "rounded-field bg-brand text-white hover:bg-brand-hover active:bg-brand-hover",
  secondary:
    "rounded-field bg-surface text-ink border border-line-strong hover:bg-surface-muted active:bg-surface-muted",
  // 🚨 예전에는 배경도 테두리도 없는 **글자만** 이었다. 옆에 secondary 가 서면 한쪽만 상자가 있어
  //    줄이 어긋나 보였고, 카드 안에서는 버튼인지 문장인지 구분이 안 됐다. 이제 셋의 실루엣이
  //    같고(같은 높이 · radius `field`), 서열은 **채움 → 옅은 채움 → 빈 칸**으로 난다.
  // 🚨 글자가 `brand` 가 아니라 `ink-muted` 다. 초록 글자를 두면 "안 할래요" 같은 **거절 버튼이
  //    옆의 실행 버튼보다 눈에 띄어** 서열이 뒤집힌다 — 실제로 그렇게 보였다. 브랜드색은
  //    실행하는 쪽(primary · approve)에만 남긴다.
  tertiary:
    "rounded-field border border-line bg-transparent text-ink-muted hover:border-line-strong hover:bg-surface-muted hover:text-ink active:border-line-strong active:bg-surface-muted active:text-ink",
  // 좌우 여백이 없는 게 아니라 전체 폭이라 필요 없던 것이다. 문구가 길어 가장자리까지
  // 닿으면 다른 변형과 같은 여백을 받는다.
  approve:
    "min-h-approve w-full px-5 rounded-field bg-brand text-white hover:bg-brand-hover active:bg-brand-hover",
  danger: "rounded-field bg-danger text-white hover:bg-danger-hover active:bg-danger-hover",
  // 외부 브랜드. 카카오가 정한 색·모양을 따라야 해서 예외로 둔 변형이다 (문서 §2-6).
  kakao: "rounded-field bg-kakao text-kakao-ink hover:bg-kakao-hover active:bg-kakao-hover",
};

/**
 * 🚨 **크기는 두 단계뿐이다.** `default`(높이 `button` 48)는 화면이 소유한 행동,
 *    `compact`(높이 `touch` 44)는 **카드 안**의 행동이다. 카드 본문이 12~14px 인데 48px 짜리
 *    상자가 들어가면 버튼이 내용보다 커 보인다 — 실제로 그렇게 보였다.
 *    44 는 터치 타깃 최소값이라(문서 §9) 더 줄이지 않는다.
 *    `approve` 는 이 축을 보지 않는다 — 승인 버튼은 항상 제일 크다.
 */
export type ButtonSize = "default" | "compact";

const SIZE: Record<ButtonSize, string> = {
  default: "min-h-button px-5",
  compact: "min-h-touch px-4",
};

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  /** 전체 폭으로 늘린다. approve 는 항상 전체 폭이라 이 값을 보지 않는다. */
  block?: boolean;
  children: ReactNode;
}

export function Button({
  variant = "primary",
  size = "default",
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
        // `py-2` 는 한 줄일 때는 보이지 않는다(최소 높이가 더 크다). 문구가 늘어나 버튼이
        // 커지기 시작하면 그때부터 글자가 위아래 가장자리에 붙지 않게 받친다.
        "text-button ease-standard inline-flex items-center justify-center gap-2 py-2 text-center transition-colors duration-120",
        // approve 는 자기 높이·폭을 스스로 갖는다 (승인 게이트 2곳 전용).
        variant === "approve" ? null : SIZE[size],
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
