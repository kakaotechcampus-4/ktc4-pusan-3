"use client";

import { Check, ChevronDown, ChevronUp } from "lucide-react";
import { useCallback, useEffect, useId, useRef, useState } from "react";

import { cn } from "@/lib/cn";

import { ICON_SIZE, ICON_STROKE } from "./icon";

/**
 * 디자인 시스템 §7 고르기 상자 — **직접 만든 드롭다운.**
 *
 * ⚠️ **네이티브 `<select>` 를 쓰지 않기로 한 자리다.** 그쪽은 키보드 · 포커스 · 스크린리더 ·
 *    바깥 클릭을 브라우저가 줘서 싸지만, 닫혀 있을 때 말고는 생김새를 우리가 못 정한다.
 *    직접 만들기로 한 이상 **그 넷이 전부 이 파일의 책임**이다 (apps/web/CLAUDE.md §3
 *    "라이브러리를 안 쓰는 대신 접근성이 전부 우리 책임이다"). 아래 🚨 는 지우지 말 것 —
 *    하나라도 빠지면 키보드·스크린리더 사용자에게 이 필터가 **없는 것과 같아진다.**
 *
 * 🚨 **ARIA 는 combobox + listbox 한 쌍이다.** 버튼이 `aria-expanded` · `aria-haspopup` ·
 *    `aria-controls` 를 지고, 목록이 `role="listbox"`, 항목이 `role="option"` +
 *    `aria-selected` 를 진다. `<div>` 에 클릭 핸들러만 달면 스크린리더에는 목록이 아니다.
 *
 * 🚨 **포커스는 열 때 들어가고 닫을 때 버튼으로 돌아온다.** 돌아오지 않으면 닫는 순간
 *    포커스가 `<body>` 로 떨어져서 키보드 사용자가 자리를 잃는다 (09 달력에서 같은 사고를 냈다).
 *
 * 🚨 **ESC · 바깥 클릭 · Tab 으로 닫힌다.** 열어 두고 스크롤하면 목록이 트리거에서 떨어지므로
 *    스크롤에도 닫는다.
 *
 * 🚨 **그림자가 없다** (문서 §6 — 그림자는 바텀시트 하나뿐). 떠 있는 면의 경계는 `line-strong`
 *    1px 이 만든다 (토스트와 같은 처리다).
 * 🚨 **등장 애니메이션이 없다** (문서 §8). 쉐브론도 회전시키지 않는다 — 상호작용 전환은
 *    "색만 바꾸고 크기·위치는 건드리지 않는다" 라서, 방향은 아이콘을 갈아 끼워 말한다.
 * 🚨 **라벨을 지우지 않는다.** 좁은 화면에서 상자만 남기면 무엇을 고르는 상자인지 사라진다.
 */
export function Select<T extends string>({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: T;
  options: ReadonlyArray<{ value: T; label: string }>;
  onChange: (value: T) => void;
}) {
  const id = useId();
  const listId = `${id}-list`;
  const labelId = `${id}-label`;

  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const listRef = useRef<HTMLUListElement>(null);

  const selectedIndex = Math.max(
    0,
    options.findIndex((option) => option.value === value),
  );
  const current = options[selectedIndex];

  /** 🚨 닫을 때는 **항상** 버튼으로 돌아온다 — 고르든 그만두든. */
  const close = useCallback((focusTrigger = true) => {
    setOpen(false);
    if (focusTrigger) triggerRef.current?.focus();
  }, []);

  // 열렸을 때만 바깥 클릭·스크롤·리사이즈를 듣는다.
  useEffect(() => {
    if (!open) return;

    const onPointerDown = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    /**
     * 🚨 목록은 트리거에 붙어 있다 — 페이지가 움직이면 자리가 어긋나므로 닫는다.
     *
     * 🚨 **목록 자신이 스크롤한 것은 빼야 한다.** 이 목록에도 `overflow-y-auto` 가 있어서,
     *    방향키로 아래 항목에 닿으면 목록이 스스로 스크롤한다. 그것까지 "페이지가 움직였다" 로
     *    치면 **긴 목록을 방향키로 내려가는 순간 닫힌다.** 트리거는 그대로 있으니 자리도
     *    안 어긋난다.
     */
    const onMove = (event: Event) => {
      if (rootRef.current?.contains(event.target as Node)) return;
      setOpen(false);
    };

    document.addEventListener("pointerdown", onPointerDown);

    /**
     * 🚨 **스크롤 감시는 한 프레임 뒤에 건다.** "페이지가 움직이면 닫는다" 는 **연 다음의**
     *    움직임을 말하는 것인데, 여는 동작 자체가 스크롤을 만든다 — 버튼을 누르면 브라우저가
     *    그 버튼을 보이게 하려고 스크롤되는 면(바텀시트 본문 등)을 움직이고, 그 스크롤이
     *    같은 틱에 감시에 걸려 **방금 연 목록을 즉시 닫는다.** 화면에서는 "눌렀는데 아무 일도
     *    안 일어난다" 로 보인다 (11 검사지 시트에서 실제로 그랬다 · 두 번 눌러야 열렸다).
     */
    const frame = requestAnimationFrame(() => {
      window.addEventListener("scroll", onMove, true);
      window.addEventListener("resize", onMove);
    });

    return () => {
      cancelAnimationFrame(frame);
      document.removeEventListener("pointerdown", onPointerDown);
      window.removeEventListener("scroll", onMove, true);
      window.removeEventListener("resize", onMove);
    };
  }, [open]);

  /**
   * 열리면 고른 항목에 포커스를 둔다 — 어디서부터 움직이는지가 보여야 한다.
   *
   * 🚨 **`preventScroll` 없이 부르면 상자가 열리자마자 도로 닫힌다.** 이 상자가 스크롤되는 면
   *    (바텀시트 본문 등) 안에 있고 아래쪽에 있으면, 브라우저가 포커스 받은 항목을 보이게
   *    하려고 **그 면을 스크롤한다.** 그 스크롤이 바로 위 `onMove` 에 걸려서 방금 연 목록을
   *    닫는다 — 화면에서는 **눌렀는데 아무 일도 안 일어나는 것**으로 보인다 (11 검사지 시트에서
   *    실제로 그랬다). 목록은 트리거에 붙어 있어 이미 보이므로 스크롤할 이유가 없다.
   */
  useEffect(() => {
    if (!open) return;
    const items = listRef.current?.querySelectorAll<HTMLLIElement>('[role="option"]');
    items?.[selectedIndex]?.focus({ preventScroll: true });
  }, [open, selectedIndex]);

  function moveFocus(from: number, delta: number) {
    const items = listRef.current?.querySelectorAll<HTMLLIElement>('[role="option"]');
    if (!items || items.length === 0) return;
    const next = Math.min(items.length - 1, Math.max(0, from + delta));
    items[next]?.focus();
  }

  function focusEdge(edge: "first" | "last") {
    const items = listRef.current?.querySelectorAll<HTMLLIElement>('[role="option"]');
    if (!items || items.length === 0) return;
    (edge === "first" ? items[0] : items[items.length - 1])?.focus();
  }

  return (
    <div ref={rootRef} className="relative flex min-w-0 flex-1 flex-col gap-1">
      <span id={labelId} className="text-caption text-ink-subtle">
        {label}
      </span>

      <button
        ref={triggerRef}
        type="button"
        // 🚨 이 넷이 스크린리더에 "펼쳐지는 목록" 이라고 말하는 전부다. `role="combobox"` 가
        //    있어야 "콤보 상자, 식사" 처럼 **고른 값까지** 읽힌다 — 없으면 그냥 버튼이다.
        role="combobox"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? listId : undefined}
        aria-labelledby={`${labelId} ${id}-value`}
        onClick={() => setOpen((prev) => !prev)}
        onKeyDown={(event) => {
          if (event.key === "ArrowDown" || event.key === "ArrowUp") {
            event.preventDefault();
            setOpen(true);
          }
        }}
        className={cn(
          "border-line-strong bg-surface text-body-sm text-ink ease-standard min-h-touch flex w-full items-center justify-between gap-2 rounded-full py-2 pr-3 pl-3.5 text-left transition-colors duration-120",
          "hover:bg-surface-muted active:bg-surface-muted",
          "border focus-visible:-outline-offset-2",
        )}
      >
        <span id={`${id}-value`} className="truncate">
          {current?.label}
        </span>
        {open ? (
          <ChevronUp
            aria-hidden
            size={ICON_SIZE.sm}
            strokeWidth={ICON_STROKE}
            className="text-ink-subtle shrink-0"
          />
        ) : (
          <ChevronDown
            aria-hidden
            size={ICON_SIZE.sm}
            strokeWidth={ICON_STROKE}
            className="text-ink-subtle shrink-0"
          />
        )}
      </button>

      {open ? (
        <ul
          ref={listRef}
          id={listId}
          role="listbox"
          aria-labelledby={labelId}
          // 🚨 그림자 대신 `line-strong` 1px 이 떠 있는 면을 만든다 (문서 §6).
          // 🚨 **안쪽 여백을 두지 않는다.** 위아래에 `py-*` 를 주면 고른 항목의 색 면이 테두리에
          //    못 닿아서, 첫 항목이 골라졌을 때 색 띠가 상자 안에 떠 있는 것처럼 보인다.
          //    항목이 곧 줄이므로 줄이 상자를 꽉 채우고, 모서리는 `overflow-hidden` 이 깎는다.
          className="border-line-strong bg-surface rounded-card absolute top-full right-0 left-0 z-30 mt-1 max-h-64 overflow-hidden overflow-y-auto overscroll-contain border"
          onKeyDown={(event) => {
            const items = Array.from(
              listRef.current?.querySelectorAll<HTMLLIElement>('[role="option"]') ?? [],
            );
            const index = items.indexOf(document.activeElement as HTMLLIElement);

            if (event.key === "Escape") {
              event.preventDefault();
              close();
            } else if (event.key === "ArrowDown") {
              event.preventDefault();
              moveFocus(index, 1);
            } else if (event.key === "ArrowUp") {
              event.preventDefault();
              moveFocus(index, -1);
            } else if (event.key === "Home") {
              event.preventDefault();
              focusEdge("first");
            } else if (event.key === "End") {
              event.preventDefault();
              focusEdge("last");
            } else if (event.key === "Tab") {
              // 🚨 Tab 은 막지 않는다 — 목록을 닫고 원래 순서대로 다음 요소에 간다.
              close(false);
            }
          }}
        >
          {options.map((option) => {
            const selected = option.value === value;

            return (
              <li
                key={option.value}
                role="option"
                aria-selected={selected}
                tabIndex={-1}
                onClick={() => {
                  onChange(option.value);
                  close();
                }}
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    onChange(option.value);
                    close();
                  }
                }}
                className={cn(
                  "text-body-sm ease-standard min-h-touch flex cursor-pointer items-center gap-2 px-3.5 py-2 transition-colors duration-120",
                  "focus-visible:-outline-offset-2",
                  // 🚨 고른 항목을 **색 하나로** 말하지 않는다 — 체크 아이콘이 같이 선다 (문서 §3).
                  // 🚨 `brand-soft` 배경 위 글자는 `brand-ink` 다 — `brand` 가 아니다.
                  //    이름이 곧 용도라 "대비만 맞으면 된다" 로 고르면 안 된다 (문서 §2 · §5).
                  selected
                    ? "bg-brand-soft text-brand-ink"
                    : "text-ink hover:bg-surface-muted active:bg-surface-muted",
                )}
              >
                <span className="min-w-0 flex-1 truncate">{option.label}</span>
                {selected ? (
                  <Check
                    aria-hidden
                    size={ICON_SIZE.sm}
                    strokeWidth={ICON_STROKE}
                    className="shrink-0"
                  />
                ) : null}
              </li>
            );
          })}
        </ul>
      ) : null}
    </div>
  );
}
