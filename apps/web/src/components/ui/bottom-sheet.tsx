"use client";

import { useEffect, useRef, type ReactNode } from "react";

import { cn } from "@/lib/cn";

/**
 * 디자인 시스템 §7 바텀시트.
 *
 * 🚨 **네이티브 `<dialog>` 위에 얹는다.** 포커스 트랩 · ESC · 바깥 요소 `inert` · 스크림을
 *    브라우저가 준다. 라이브러리를 안 쓰는 대신 접근성이 전부 우리 책임인데(§3),
 *    이 넷을 손으로 짜면 반드시 어딘가 빠진다.
 *
 * 🚨 `dismissible: false` 는 **승인 시트 전용**이다. 스크림 탭·ESC 로 닫히지 않는다 —
 *    실수로 닫혀서 draft 가 만료되는 경로를 만들지 않는다(승인 없는 draft 는 24시간 뒤 만료).
 *    법적 고지처럼 되돌릴 수 있는 것에는 쓰지 않는다.
 */
export function BottomSheet({
  open,
  onClose,
  title,
  description,
  dismissible = true,
  children,
  footer,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  description?: string;
  /** 🚨 승인 시트만 false. 그 외에는 닫을 길을 막지 않는다. */
  dismissible?: boolean;
  children: ReactNode;
  footer?: ReactNode;
}) {
  const ref = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal();
    if (!open && dialog.open) dialog.close();
  }, [open]);

  // showModal 이 바깥 조작은 막아 주지만 배경 스크롤까지 막지는 않는다.
  useEffect(() => {
    if (!open) return;
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = previous;
    };
  }, [open]);

  return (
    <dialog
      ref={ref}
      aria-labelledby="bottom-sheet-title"
      onCancel={(e) => {
        // ESC. 승인 시트면 브라우저 기본 닫기를 막는다.
        if (!dismissible) e.preventDefault();
        else onClose();
      }}
      onClick={(e) => {
        // 스크림(= dialog 자신)을 눌렀을 때만. 내용은 안쪽 div 가 받는다.
        if (dismissible && e.target === ref.current) onClose();
      }}
      className={cn(
        // dialog 기본 위치·크기를 지우고 화면 아래에 붙인다.
        "bg-canvas text-ink max-w-content max-h-[88dvh] w-full",
        "rounded-t-sheet shadow-sheet mx-auto mt-auto mb-0 p-0",
        "backdrop:bg-scrim",
      )}
    >
      <div className="flex max-h-[88dvh] flex-col">
        <div className="flex flex-col items-center gap-4 px-5 pt-3">
          <span aria-hidden className="bg-line-strong h-1 w-9 rounded-full" />
          <div className="w-full">
            <h2 id="bottom-sheet-title" className="text-title text-ink">
              {title}
            </h2>
            {description ? <p className="text-body-sm text-ink-muted mt-1">{description}</p> : null}
          </div>
        </div>

        {/* 넘치면 화면이 아니라 시트 안에서만 스크롤한다 (§7). */}
        <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-5 py-4">
          {children}
        </div>

        {/* 🚨 pb-safe-4 는 safe area + 16px 을 한 속성에 합친다. py-4 와 나란히 쓰면
            아래 패딩이 죽어서 버튼이 화면 맨 아래에 붙는다 (실제로 그랬다). */}
        {footer ? <div className="border-line pb-safe-4 border-t px-5 pt-4">{footer}</div> : null}
      </div>
    </dialog>
  );
}
