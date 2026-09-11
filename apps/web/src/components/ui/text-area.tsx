"use client";

import { useEffect, useRef, type TextareaHTMLAttributes } from "react";
import { useId } from "react";

import { cn } from "@/lib/cn";

/**
 * 디자인 시스템 §7 `textarea` — 03 홈의 한 줄 입력.
 *
 * 최소 높이 96 · 자동 증가 · 5줄이 넘으면 그때부터 안에서 스크롤한다.
 * "한 줄" 이라고 부르지만 부모는 실제로 서너 줄을 쓴다 — 쓰는 동안 자기 글이 안 보이면
 * 무엇을 적었는지 확인하러 스크롤해야 한다.
 *
 * ⚠️ 글자 크기를 16px 미만으로 내리지 않는다 (`text-body`).
 *    iOS 웹뷰는 16px 미만 입력에 포커스가 가면 화면을 자동 확대하고 그대로 남는다 (문서 §4).
 *
 * 🚨 `failed` 이벤트가 오면 `raw_text` 를 여기에 그대로 되돌려 놓는다.
 *    부모가 다시 타이핑하게 만들지 않는다 (CLAUDE.md §2).
 */

/** 96px. 자동 증가의 바닥값이라 CSS 클래스가 아니라 숫자로 들고 있어야 한다. */
const MIN_HEIGHT_PX = 96;
/** 5줄 = 16px × 1.6 × 5 + 위아래 여백. 이보다 길어지면 늘리지 않고 안에서 스크롤한다. */
const MAX_HEIGHT_PX = 152;
/** 채팅바의 바닥값. 옆의 아이콘 버튼(`touch` 44)과 같은 줄에 서야 한다. */
const BARE_MIN_HEIGHT_PX = 44;

/**
 * `field` = 테두리가 있는 보통 입력. `bare` = 테두리 없이 다른 상자 안에 들어가는 입력
 * (03 채팅바). 🚨 bare 를 단독으로 쓰지 않는다 — 감싸는 상자가 입력처럼 보여야 한다.
 */
type TextAreaVariant = "field" | "bare";

const VARIANT: Record<TextAreaVariant, string> = {
  field:
    "rounded-field bg-surface border-line-strong hover:border-ink-subtle ease-standard border px-3.5 py-3 transition-colors duration-120",
  bare: "border-0 bg-transparent px-1 py-2 focus-visible:outline-none",
};

interface TextAreaProps extends Omit<TextareaHTMLAttributes<HTMLTextAreaElement>, "id" | "rows"> {
  label: string;
  /** 라벨을 화면에서 숨긴다. 스크린리더에는 남는다 — 라벨 없는 입력을 만들지 않는다. */
  labelHidden?: boolean;
  hint?: string;
  value: string;
  variant?: TextAreaVariant;
  /** 자동 증가의 천장. 채팅바처럼 좁은 자리에서는 낮춘다. */
  maxHeightPx?: number;
}

export function TextArea({
  label,
  labelHidden = false,
  hint,
  className,
  value,
  variant = "field",
  maxHeightPx = MAX_HEIGHT_PX,
  ...props
}: TextAreaProps) {
  const id = useId();
  const hintId = `${id}-hint`;
  const ref = useRef<HTMLTextAreaElement>(null);
  // 채팅바는 한 줄에서 시작한다 — 96px 짜리 빈 상자가 화면 아래를 차지하면 본문이 밀린다.
  const minHeight = variant === "bare" ? BARE_MIN_HEIGHT_PX : MIN_HEIGHT_PX;

  // 값이 바뀔 때마다 다시 잰다. 입력 중뿐 아니라 raw_text 복원처럼 밖에서 값을 넣을 때도
  // 높이가 맞아야 해서 onChange 가 아니라 value 를 본다.
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(Math.max(el.scrollHeight, minHeight), maxHeightPx)}px`;
  }, [value, minHeight, maxHeightPx]);

  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={id} className={cn("text-label text-ink-muted", labelHidden && "sr-only")}>
        {label}
      </label>
      {hint ? (
        <p id={hintId} className="text-caption text-ink-subtle">
          {hint}
        </p>
      ) : null}
      <textarea
        ref={ref}
        id={id}
        value={value}
        aria-describedby={hint ? hintId : undefined}
        style={{ minHeight, maxHeight: maxHeightPx }}
        className={cn(
          "text-body text-ink placeholder:text-ink-subtle w-full resize-none",
          VARIANT[variant],
          className,
        )}
        {...props}
      />
    </div>
  );
}
