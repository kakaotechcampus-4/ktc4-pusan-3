"use client";

import { useEffect, useId, useRef, type ReactNode } from "react";

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
 *
 * 🚨 `variant="document"` 는 **약관·동의 전문 전용**이다. 시트를 통째로 `font-doc`(Pretendard)
 *    으로 바꾼다 — 제목만 손글씨로 남기면 `size-adjust` 때문에 같은 시트 안에서 글자 크기감이
 *    어긋나고, 무엇보다 "한 화면 한 서체" 가 깨진다 (디자인 시스템 §4). 본문 서체는 단일
 *    웨이트라 굵기가 브라우저 합성인데, 불리한 조항을 놓치지 않고 읽어야 하는 글에서는
 *    그 교환(가독성↓ 온기↑)을 하지 않는다.
 */
export function BottomSheet({
  open,
  onClose,
  title,
  description,
  dismissible = true,
  variant = "default",
  children,
  footer,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  description?: string;
  /** 🚨 승인 시트만 false. 그 외에는 닫을 길을 막지 않는다. */
  dismissible?: boolean;
  /** 🚨 `document` 는 약관·동의 전문 전용. 시트 전체가 `font-doc` 이 된다. */
  variant?: "default" | "document";
  children: ReactNode;
  footer?: ReactNode;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  /**
   * 🚨 **제목 id 를 상수로 박지 않는다.** 한 화면이 시트를 여러 개 마운트하는데(10 설정은 다섯),
   *    `<dialog>` 는 닫혀 있어도 DOM 에 남는다. 같은 id 가 여럿이면 `aria-labelledby` 가 문서에서
   *    **먼저 나오는** 것을 집어서, 연 시트가 다른 시트의 제목으로 읽히거나(초대 시트 제목으로
   *    읽힌 연결 끊기 시트) 이름이 통째로 비어 버린다.
   */
  const titleId = useId();

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
      aria-labelledby={titleId}
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
        variant === "document" && "font-doc",
        "rounded-t-sheet shadow-sheet mx-auto mt-auto mb-0 p-0",
        "backdrop:bg-scrim",
      )}
    >
      <div className="flex max-h-[88dvh] flex-col">
        <div className="flex flex-col items-center gap-4 px-5 pt-3">
          <span aria-hidden className="bg-line-strong h-1 w-9 rounded-full" />
          <div className="w-full">
            <h2 id={titleId} className="text-title text-ink">
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
