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
 * 🚨 **ESC · 바깥 클릭 · Tab 으로 닫힌다. 스크롤에는 닫지 않는다.** 목록은 루트 안의 `absolute`
 *    라서 본문이든 페이지든 스크롤되면 트리거와 같이 움직인다 — 떨어질 일이 없다. 스크롤에 닫으면
 *    바텀시트처럼 스크롤되는 면 안에서 **본문 바닥에 잘린 항목을 고를 길이 없어진다** (#306 ·
 *    잘린 항목을 보려고 본문을 미는 순간 목록이 닫혔다). 🚨 목록을 포털이나 `fixed` 로 옮기면
 *    이 전제가 깨진다 — 그때는 자리를 다시 재는 쪽으로 간다.
 *
 * 🚨 **열 때 목록이 보이게 가장 가까운 스크롤 면을 움직인다.** 시트 아래쪽 상자는 목록이 본문
 *    바닥에 잘린 채로 열리는데, 보호자가 그걸 알아채고 스크롤해서 찾게 두지 않는다.
 *
 * 🚨 **그림자가 없다** (문서 §6 — 그림자는 바텀시트 하나뿐). 떠 있는 면의 경계는 `line-strong`
 *    1px 이 만든다 (토스트와 같은 처리다).
 * 🚨 **등장 애니메이션이 없다** (문서 §8). 쉐브론도 회전시키지 않는다 — 상호작용 전환은
 *    "색만 바꾸고 크기·위치는 건드리지 않는다" 라서, 방향은 아이콘을 갈아 끼워 말한다.
 * 🚨 **라벨을 지우지 않는다.** 좁은 화면에서 상자만 남기면 무엇을 고르는 상자인지 사라진다.
 *    ⚠️ `labelHidden` 은 그 예외가 아니라 **자리를 옮기는 것**이다 — 라벨은 DOM 에 그대로 남아
 *    (`sr-only`) 보조기술이 읽고, 눈에 보이는 이름은 **바깥 묶음**이 진다. 시각 칸(`TimeField`)
 *    처럼 상자 둘이 한 값을 이루고 값 자체가 단위를 지고 있을 때만 쓴다("오전 9시" · "05분").
 *    상자 하나가 홀로 설 때는 쓰지 않는다 — 그때는 위 규칙 그대로다.
 *
 * 🚨 **`placeholder` 는 "아직 안 골랐다" 를 말한다.** 이 값이 없으면 목록에 없는 `value` 가 와도
 *    **첫 항목이 골라진 것처럼** 보인다 (`findIndex` 가 -1 이라 0 으로 떨어졌다) — 아무도 고르지
 *    않은 값이 화면에 서는 길이라, 고르지 않은 상태가 있는 칸은 이걸 반드시 넘긴다.
 *
 * ⚠️ **`shape` 는 이 상자가 어디에 서는가다.** 기본값 `pill` 은 §7 "고르기 상자" 사양
 *    (`full` · `min-h-touch`) 그대로고, `field` 는 **폼 칸**이라 `input`·`date-field` 와 같은
 *    모양·높이(`rounded-field` · `min-h-field`)를 쓴다. 한 폼 안에서 한 칸만 알약이고 8px 낮으면
 *    그 칸이 입력이 아니라 필터로 읽힌다 (문서 §7 "`min-h-field` 다" 와 같은 이유).
 *
 * 🚨 **사유(`error`)는 상자에 붙인다** (`TextInput` 과 같은 처리 · 문서 §7 입력).
 *    상자 밖에 떠 있는 문단으로 두면 보조기술이 그 문구를 **어느 칸의 문제인지** 잇지 못한다.
 *    `aria-invalid` 와 `aria-describedby` 를 함께 건다 — 색(`danger` 테두리)만으로는
 *    단독 신호가 되고, 안 고른 것을 "고르지 않음" 으로 넘길 수 있는 칸에서는 그게 곧
 *    **빈 값이 조용히 저장되는 길**이다.
 */
export function Select<T extends string>({
  label,
  value,
  options,
  onChange,
  error,
  labelHidden = false,
  placeholder,
  shape = "pill",
}: {
  label: string;
  /** 🚨 빈 문자열은 "아직 안 골랐다" 다 — 그때는 `placeholder` 가 선다. */
  value: T | "";
  options: ReadonlyArray<{ value: T; label: string }>;
  onChange: (value: T) => void;
  /** 고르지 않았거나 잘못 고른 이유. 🚨 상자 밖 문단으로 대신하지 않는다 (위 머리말). */
  error?: string | null;
  /** 라벨을 `sr-only` 로 옮긴다. 🚨 지우는 것이 아니다 (위 머리말). */
  labelHidden?: boolean;
  /** 아직 안 골랐을 때의 문구. 🚨 없으면 첫 항목이 골라진 것처럼 보인다 (위 머리말). */
  placeholder?: string;
  /** 필터·고르기 자리는 `pill`, 폼 칸은 `field` (위 머리말). */
  shape?: "pill" | "field";
}) {
  const id = useId();
  const listId = `${id}-list`;
  const labelId = `${id}-label`;
  const errorId = `${id}-error`;

  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const listRef = useRef<HTMLUListElement>(null);

  const index = options.findIndex((option) => option.value === value);
  const current = index < 0 ? undefined : options[index];
  /** 🚨 안 골랐으면 **첫 항목에서** 시작한다 — 포커스는 어딘가에 있어야 한다. 고른 것은 아니다. */
  const selectedIndex = Math.max(0, index);

  /** 🚨 닫을 때는 **항상** 버튼으로 돌아온다 — 고르든 그만두든. */
  const close = useCallback((focusTrigger = true) => {
    setOpen(false);
    if (focusTrigger) triggerRef.current?.focus();
  }, []);

  // 열렸을 때만 바깥 클릭을 듣는다. 🚨 스크롤 · 리사이즈는 듣지 않는다 (위 머리말 · #306).
  useEffect(() => {
    if (!open) return;

    const onPointerDown = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };

    document.addEventListener("pointerdown", onPointerDown);
    return () => document.removeEventListener("pointerdown", onPointerDown);
  }, [open]);

  /**
   * 열리면 목록 전체가 보이게 한 뒤 고른 항목에 포커스를 둔다 — 어디서부터 움직이는지가 보여야 한다.
   *
   * 🚨 **목록을 먼저 보이게 하고, 포커스는 `preventScroll` 로 준다.** 포커스가 스크롤을 맡으면
   *    브라우저는 **그 항목 하나**만 보이게 움직여서, 첫 항목이 골라져 있으면 그 아래가 본문 바닥에
   *    잘린 채로 남는다 (#306). `block: "nearest"` 라 이미 다 보이면 아무것도 움직이지 않는다.
   */
  useEffect(() => {
    if (!open) return;
    listRef.current?.scrollIntoView({ block: "nearest" });
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
      <span id={labelId} className={labelHidden ? "sr-only" : "text-caption text-ink-subtle"}>
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
        aria-invalid={error ? true : undefined}
        aria-describedby={error ? errorId : undefined}
        onClick={() => setOpen((prev) => !prev)}
        onKeyDown={(event) => {
          if (event.key === "ArrowDown" || event.key === "ArrowUp") {
            event.preventDefault();
            setOpen(true);
          }
        }}
        className={cn(
          "bg-surface text-ink ease-standard flex w-full items-center justify-between gap-2 py-2 pr-3 pl-3.5 text-left transition-colors duration-120",
          "border focus-visible:-outline-offset-2",
          // 🚨 `cn()` 은 tailwind-merge 가 아니다 — 모양마다 **통째로** 고른다.
          shape === "field"
            ? "text-body rounded-field min-h-field"
            : "text-body-sm min-h-touch rounded-full",
          // 🚨 누른 느낌도 모양을 따라간다 — 폼 칸은 `date-field` 와 같은 테두리 반응이다.
          shape === "field"
            ? "hover:border-ink-subtle"
            : "hover:bg-surface-muted active:bg-surface-muted",
          error ? "border-danger" : "border-line-strong",
        )}
      >
        {/* 🚨 안 고른 자리는 `ink-subtle` 이다 — `date-field` 의 placeholder 와 같은 처리다. */}
        <span id={`${id}-value`} className={cn("truncate", current ? null : "text-ink-subtle")}>
          {current?.label ?? placeholder}
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

      {error ? (
        <p id={errorId} className="text-caption text-danger-ink">
          {error}
        </p>
      ) : null}

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
