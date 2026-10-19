"use client";

import { useEffect, useId, useRef, useState, type ReactNode } from "react";

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
 * 🚨 **모든 시트가 같은 움직임으로 열리고 닫힌다** (`globals.css` 의 `.bottom-sheet`). 승인 게이트를
 *    담은 시트도 예외가 아니다 — 시트마다 움직임이 있고 없고가 갈리면 그 차이가 더 어색하다 (#490).
 *    게이트가 사람 앞에 서는 것은 움직임을 빼서가 아니라 그 안의 `btn-approve` · `caution` 이 말한다.
 *
 * 🚨 **닫히는 동안에는 열려 있던 마지막 모습을 그린다.** 부르는 쪽은 닫으면서 넘기던 값을 지우는
 *    일이 많다 (`target` → `null`, 폼 `reset()`). 그대로 그리면 시트가 내려가는 200ms 동안 제목이
 *    비고 본문이 먼저 사라진다. 부르는 쪽마다 지우는 시점을 미루게 하지 않고 여기서 한 번 막는다.
 * 🚨 **닫힘이 끝나면 그 모습을 놓는다.** 계속 들고 있으면 다음에 열 때 React 가 지난번 인스턴스를
 *    재사용해서, 버린 입력 · 넘겨 둔 달력 · 다른 기록에서 누른 교정 확인이 그대로 이어진다.
 *    부르는 쪽은 "닫히면 내용을 버린다" 에 기대도 된다 (`open && ...`, `key` 로 새로 만들기).
 *    닫힘이 끝나기 전에 다시 열어도 같다 — 내려가던 내용을 버리고 새로 만든다 (`generation`).
 *
 * 🚨 `variant="document"` 는 **약관·동의 전문 전용**이다. 시트를 통째로 `font-doc`(Pretendard)
 *    으로 바꾼다 — 제목만 손글씨로 남기면 `size-adjust` 때문에 같은 시트 안에서 글자 크기감이
 *    어긋나고, 무엇보다 "한 화면 한 서체" 가 깨진다 (디자인 시스템 §4). 손글씨는 자소
 *    판별성이 낮은데, 불리한 조항을 놓치지 않고 읽어야 하는 글에서는 그 교환
 *    (가독성↓ 온기↑)을 하지 않는다.
 */
/** 닫는 전환이 끝났어야 할 시각에 더 기다리는 여유. 전환을 못 그리는 브라우저에서만 이 타이머가 놓는다. */
const RELEASE_SLACK_MS = 200;

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

  /**
   * 열려 있는 동안의 마지막 모습. 🚨 `ref` 가 아니라 state 다 — 그리는 중에 ref 를 읽고 쓰면
   * React 가 결과를 보장하지 않는다. 이전 렌더의 값을 기억하는 공식 방법이 이 모양이다
   * (`photo-source-sheet.tsx` 의 `wasOpen` 과 같다).
   */
  const live = { title, description, variant, children, footer };
  const [held, setHeld] = useState<typeof live | null>(null);
  if (
    open &&
    (held === null ||
      held.title !== title ||
      held.description !== description ||
      held.variant !== variant ||
      held.children !== children ||
      held.footer !== footer)
  ) {
    setHeld(live);
  }
  const shown = !open && held ? held : live;

  /**
   * 🚨 **닫히는 중에 다시 열리면 내용을 새로 만든다.** 아직 놓지 않은 모습과 새로 연 내용이 모양이 같으면
   *    React 가 인스턴스를 재사용해서, 기록 A 에서 눌러 둔 교정 확인이 바로 누른 기록 B 에 이어진다.
   *    닫히는 시트는 누르기를 받지 않아(`globals.css`) 아래 목록을 200ms 안에 다시 누를 수 있다.
   */
  const [wasOpen, setWasOpen] = useState(open);
  const [generation, setGeneration] = useState(0);
  if (open !== wasOpen) {
    setWasOpen(open);
    if (open && held !== null) setGeneration((n) => n + 1);
  }

  /**
   * 닫는 움직임이 끝나면 들고 있던 모습을 놓는다 (머리말). `transitionend` 를 기다리되, 전환을 못 그리는
   * 브라우저에서는 이벤트가 안 오므로 시간으로도 놓는다 — 지금 걸린 전환 시간에 여유를 더해서.
   */
  const closing = !open && held !== null;
  useEffect(() => {
    const dialog = ref.current;
    if (!closing || !dialog) return;
    const release = () => setHeld(null);
    const onEnd = (e: TransitionEvent) => {
      if (e.target === dialog) release();
    };
    dialog.addEventListener("transitionend", onEnd);
    // 🚨 시간은 상수가 아니라 지금 걸린 전환에서 읽는다 — 닫힘 토큰이 길어지면 내려가는 중에 본문이 사라진다.
    const seconds = Math.max(
      0,
      ...getComputedStyle(dialog)
        .transitionDuration.split(",")
        .map((value) => parseFloat(value) || 0),
    );
    const fallback = setTimeout(release, seconds * 1000 + RELEASE_SLACK_MS);
    return () => {
      dialog.removeEventListener("transitionend", onEnd);
      clearTimeout(fallback);
    };
  }, [closing]);

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
      // 🚨 **닫히는 동안에는 보조기술에서 뺀다.** 닫는 움직임(`base`) 동안 시트는 화면에 남아 있어서, 그대로 두면
      //    바로 다음에 열린 시트와 같은 이름의 대화상자가 둘 읽힌다 (11 알레르기: 고르는 시트 → 적는 시트가 제목이 같다).
      aria-hidden={open ? undefined : true}
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
        shown.variant === "document" && "font-doc",
        "bottom-sheet",
        "rounded-t-sheet shadow-sheet mx-auto mt-auto mb-0 p-0",
        "backdrop:bg-scrim",
      )}
    >
      <div key={generation} className="flex max-h-[88dvh] flex-col">
        <div className="flex flex-col items-center gap-4 px-5 pt-3">
          <span aria-hidden className="bg-line-strong h-1 w-9 rounded-full" />
          <div className="w-full">
            <h2 id={titleId} className="text-title text-ink">
              {shown.title}
            </h2>
            {shown.description ? (
              <p className="text-body-sm text-ink-muted mt-1">{shown.description}</p>
            ) : null}
          </div>
        </div>

        {/* 넘치면 화면이 아니라 시트 안에서만 스크롤한다 (§7). */}
        <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-5 py-4">
          {shown.children}
        </div>

        {/* 🚨 pb-safe-4 는 safe area + 16px 을 한 속성에 합친다. py-4 와 나란히 쓰면
            아래 패딩이 죽어서 버튼이 화면 맨 아래에 붙는다 (실제로 그랬다). */}
        {shown.footer ? (
          <div className="border-line pb-safe-4 border-t px-5 pt-4">{shown.footer}</div>
        ) : null}
      </div>
    </dialog>
  );
}
