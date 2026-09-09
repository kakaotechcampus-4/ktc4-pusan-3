"use client";

import { Check } from "lucide-react";
import type { ReactNode } from "react";
import { useId } from "react";

import { ICON_STROKE } from "@/components/ui/icon";
import { cn } from "@/lib/cn";

/**
 * 디자인 시스템 §7 `checkbox`.
 *
 * 네이티브 `<input type="checkbox">` 를 `sr-only` 로 숨기고 표식만 그린다 —
 * 키보드·스크린리더·폼 의미를 브라우저에서 그대로 받기 위해서다
 * (라이브러리를 안 쓰는 대신 접근성이 전부 우리 책임이다 · apps/web/CLAUDE.md §3).
 *
 * 행 전체가 터치 타깃이라 라벨 아무 곳이나 눌러도 토글된다 (§9 최소 44px).
 */
export function Checkbox({
  checked,
  onChange,
  label,
  description,
  className,
}: {
  checked: boolean;
  onChange: (checked: boolean) => void;
  label: ReactNode;
  description?: ReactNode;
  className?: string;
}) {
  const id = useId();
  const descriptionId = description ? `${id}-description` : undefined;

  return (
    <label
      htmlFor={id}
      className={cn("group flex min-h-11 cursor-pointer items-start gap-3 py-2", className)}
    >
      <input
        id={id}
        type="checkbox"
        checked={checked}
        aria-describedby={descriptionId}
        onChange={(e) => onChange(e.target.checked)}
        className="peer sr-only"
      />
      <span
        aria-hidden
        className={cn(
          "ease-standard mt-0.5 flex size-6 shrink-0 items-center justify-center rounded-full border transition-colors duration-120",
          "peer-focus-visible:outline-brand peer-focus-visible:outline-2 peer-focus-visible:outline-offset-2",
          checked
            ? "bg-brand border-brand text-white"
            : // 아직 안 고른 것만 호버에 반응한다 — 고른 것은 이미 브랜드색이라 더 강조할 게 없다.
              "border-line-strong group-hover:border-brand group-hover:bg-brand-soft text-transparent",
        )}
      >
        <Check size={16} strokeWidth={ICON_STROKE} />
      </span>
      <span className="flex flex-col gap-1">
        <span className="text-body text-ink">{label}</span>
        {description ? (
          <span id={descriptionId} className="text-caption text-ink-subtle">
            {description}
          </span>
        ) : null}
      </span>
    </label>
  );
}
