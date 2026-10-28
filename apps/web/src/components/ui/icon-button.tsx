import Link from "next/link";
import type { ButtonHTMLAttributes, ReactNode, Ref } from "react";

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
  // 켜고 끄는 버튼(`aria-pressed`)이 켜져 있으면 눌린 모양 그대로 둔다 — 채팅바의 말로 적기가 듣는 동안.
  ghost:
    "text-ink-muted hover:bg-brand-soft hover:text-brand-ink active:bg-brand-soft active:text-brand-ink aria-pressed:bg-brand-soft aria-pressed:text-brand-ink disabled:bg-transparent disabled:text-ink-subtle",
  // 🚨 비활성 배경이 `surface-muted` 가 아니라 `surface` 다. 이 버튼이 앉는 채팅바가
  //    `surface-muted` 라, 같은 색이면 **버튼이 통째로 사라진다** (실제로 안 보였다).
  brand:
    "bg-brand text-white hover:bg-brand-hover active:bg-brand-hover disabled:bg-surface disabled:text-ink-subtle disabled:border disabled:border-line",
};

/** 두 컴포넌트가 나눠 쓰는 모양. 🚨 한 벌로 둔다 — 두 벌이 되면 한쪽만 고쳐진다. */
const SHAPE =
  "h-touch ease-standard flex aspect-square shrink-0 items-center justify-center rounded-full transition-colors duration-120";

interface IconButtonProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, "children"> {
  /** 스크린리더가 읽을 이름. 눈에 보이지 않으므로 생략할 수 없다. */
  label: string;
  tone?: IconButtonTone;
  /**
   * 포커스를 되돌려 받을 때만 쓴다 (React 19 — `ref` 가 그냥 prop 이다 · `Button` 과 같다).
   * 예: 인라인 확인 띠를 "그만두기" 로 닫으면 띠째 버튼이 사라지므로, 띠를 연 버튼으로 돌려준다 (09 일기 지우기).
   */
  ref?: Ref<HTMLButtonElement>;
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
      className={cn(SHAPE, TONE[tone], className)}
      {...props}
    >
      {children}
    </button>
  );
}

/**
 * 아이콘만 있는 **링크**. 화면 왼쪽 위의 돌아가기 화살표가 이것이다.
 *
 * 🚨 **`<button onClick={router.back()}>` 로 만들지 않는다.** 뒤로가기 히스토리는 **어디서
 *    왔는지에 따라 달라진다** — 알림이나 링크로 이 화면에 바로 들어오면 돌아갈 데가 없다.
 *    가는 곳을 `href` 로 못박으면 어디서 왔든 같은 자리로 간다. `<a>` 라서 웹뷰 뒤로가기·
 *    길게 누르기 같은 브라우저 관례도 그대로 붙는다 (`ButtonLink` 와 같은 이유).
 *
 * 🚨 `label` 은 여기서도 필수다 — 글자가 없으니 스크린리더에 남는 것이 그것뿐이다.
 *    **"뒤로" 가 아니라 가는 곳을 적는다** ("설정으로 돌아가기").
 */
export function IconButtonLink({
  href,
  label,
  tone = "ghost",
  className,
  transitionTypes,
  children,
}: {
  href: string;
  label: string;
  tone?: IconButtonTone;
  className?: string;
  /** 이 이동이 어떤 화면 전환인지 (`lib/view-transition.ts` 의 `TRANSITION_TYPE`). 없으면 움직임 없음. */
  transitionTypes?: string[];
  children: ReactNode;
}) {
  return (
    <Link
      href={href}
      aria-label={label}
      title={label}
      transitionTypes={transitionTypes}
      className={cn(SHAPE, TONE[tone], className)}
    >
      {children}
    </Link>
  );
}

/**
 * 아직 그릴지 모르는 `IconButton` 의 자리. 머리줄의 행동(초대 · 고치기)은 데이터가 와야 그릴지가 정해지는데,
 * 그때 줄 높이가 18 → 36 으로 커지면 아래가 전부 밀린다 (CLS). 받는 동안만 같은 크기의 빈 자리를 둔다.
 * 🚨 누를 수 없는 버튼을 그리지 않는다 — 보이지 않는 빈 자리다.
 */
export function IconButtonPlaceholder({ className }: { className?: string }) {
  return <span aria-hidden className={cn("h-touch aspect-square shrink-0", className)} />;
}
