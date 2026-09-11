import type { ReactNode } from "react";

import { cn } from "@/lib/cn";

/**
 * 디자인 시스템 §7 `chip-choice` — 눌러서 고르는 칩.
 *
 * 보이는 높이는 `chip` 이지만 위아래 투명 여백을 둬서 누르는 영역은 `touch` 를 채운다 (문서 §9).
 * 그래서 칩 줄은 가로 gap 만 주고 세로 gap 은 주지 않는다 — 여백이 이미 있다.
 * 둘 다 최소 높이라 글자를 키우면 같이 커진다 (문서 §10).
 *
 * 🚨 도메인 색을 여기 쓰지 않는다. 도메인 칩(`chip-domain`)은 아이콘+라벨이 붙는 다른 물건이다.
 */
export function Chip({
  selected = false,
  onClick,
  disabled,
  children,
}: {
  selected?: boolean;
  onClick: () => void;
  disabled?: boolean;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      aria-pressed={selected}
      disabled={disabled}
      onClick={onClick}
      className="min-h-touch flex items-center py-2"
    >
      <span
        className={cn(
          "text-label ease-standard min-h-chip flex items-center rounded-full border px-2.5 py-0.5 text-left transition-colors duration-120",
          selected
            ? "bg-brand-soft border-brand text-brand-ink"
            : "bg-surface border-line text-ink-muted hover:border-line-strong hover:bg-surface-muted",
          disabled && "bg-surface-muted border-line text-ink-subtle",
        )}
      >
        {children}
      </span>
    </button>
  );
}

/** 칩 줄. 넘치면 줄바꿈한다 — 가로 스크롤하지 않는다 (문서 §7). */
export function ChipRow({ children }: { children: ReactNode }) {
  return <div className="flex flex-wrap gap-x-2">{children}</div>;
}
