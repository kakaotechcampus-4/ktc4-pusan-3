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
          // 🚨 `cn()` 은 tailwind-merge 가 아니다 — 아래 세 줄이 같은 속성(bg·border·text)을 던지면
          //    클래스 순서가 아니라 생성 CSS 순서가 이긴다. 그래서 **한 번에 하나만** 고른다.
          //    (선택된 칩을 비활성으로 만드는 경우가 실제로 있다 — 07 피드백 전송 중이다.)
          disabled
            ? "bg-surface-muted border-line text-ink-subtle"
            : selected
              ? "bg-brand-soft border-brand text-brand-ink"
              : // 🚨 `active:` 를 빠뜨리면 웹뷰에서 눌러도 아무 반응이 없다 (apps/web/CLAUDE.md §5).
                //    `hover:` 는 hover 가 되는 기기에서만 걸린다 — 이 화면은 대부분 웹뷰다.
                "bg-surface border-line text-ink-muted hover:border-line-strong hover:bg-surface-muted active:border-line-strong active:bg-surface-muted",
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

/**
 * 디자인 시스템 §7 `chip-evidence` · `chip-evidence-stale` — 추천이 쓴 근거 하나.
 *
 * 🚨 **누를 수 없다.** 근거를 눌러 07 상세로 가는 경로는 07 화면 이슈 것이고,
 *    지금 `<button>` 으로 만들어 두면 아무 데도 안 가는 버튼이 된다.
 *
 * 🚨 `is_stale` (6개월 넘음) 은 점선 + 더 옅은 글자다. 단독 근거로 쓰지 않는다는 규칙(NF-08)은
 *    화면이 아니라 **서버**가 지키지만, 섞여 들어온 것을 부모가 알아볼 수 있어야 한다.
 *    색만으로 구분하지 않으려고 테두리 모양을 바꾼다 (문서 §10).
 */
export function EvidenceChip({
  label,
  meta,
  stale = false,
}: {
  label: string;
  /** "3일 전 · 보호자 직접" 같은 한 줄. 서버가 만든 문구를 그대로 받는다. */
  meta?: string;
  stale?: boolean;
}) {
  return (
    <span
      className={cn(
        "text-caption min-h-chip inline-flex items-center gap-1 rounded-full border px-2.5 py-0.5",
        stale
          ? "border-line text-ink-subtle border-dashed"
          : "border-line bg-surface text-ink-muted",
      )}
    >
      {label}
      {meta ? <span className="text-ink-subtle">· {meta}</span> : null}
      {/* 점선만으로는 무엇이 다른지 모른다 — 모양과 글자를 같이 낸다 (문서 §10). */}
      {stale ? <span className="text-ink-subtle">· 오래된 기록</span> : null}
    </span>
  );
}

/**
 * 디자인 시스템 §7 `chip-count` — "기록 12건".
 *
 * 배경도 테두리도 없다. 칩이라고 부르지만 실제로는 **건수를 칩 줄에 섞어 놓기 위한 글자**다 —
 * 근거 칩 옆에서 같은 높이로 서야 줄이 흐트러지지 않는다.
 */
export function CountChip({ children }: { children: ReactNode }) {
  return (
    <span className="text-caption text-ink-subtle min-h-chip inline-flex items-center px-0.5">
      {children}
    </span>
  );
}

/**
 * 근거 칩 줄. `ChipRow` 와 달리 **세로 gap 을 준다** — 근거 칩은 누를 수 없어서
 * 누르는 영역을 채우는 투명 여백이 없고, 그대로 두면 줄바꿈됐을 때 칩끼리 붙는다.
 */
export function EvidenceRow({ children }: { children: ReactNode }) {
  return <div className="flex flex-wrap items-center gap-x-2 gap-y-1.5">{children}</div>;
}
