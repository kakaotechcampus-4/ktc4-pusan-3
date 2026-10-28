"use client";

import { ChevronLeft, ChevronRight } from "lucide-react";

import { cn } from "@/lib/cn";

import { ICON_SIZE, ICON_STROKE } from "./icon";
import { IconButton } from "./icon-button";

/** 한 번에 보이는 쪽 번호 수. 화살표 둘과 합쳐 일곱 칸 × 44px = 308px 이라 360px 폭(본문 328px)에 든다. */
const WINDOW = 5;

/**
 * 디자인 시스템 §7 쪽 넘기기 — 번호 쪽 목록의 아래에 서는 한 줄 (#584 · 07 기록 · 기억).
 *
 * **`[이전] 4 5 [6] 7 8 [다음]`** — 지금 쪽을 가운데 둔 번호 다섯 개가 따라 움직이는 창이다.
 * 처음과 끝에서는 창이 그 끝에 붙는다(`1 2 3 4 5` · `8 9 10 11 12`).
 *
 * ⚠️ **고르기 상자를 쓰다 바꿨다.** 상자는 줄에서 가장 큰 물건이라 제일 자주 누르는 이전/다음보다 눈에 띄었고,
 *    쪽이 수십 개면 256px 목록 안에서 번호를 찾아 밀어야 했다. 이 화면에서 "특정 시점으로 가기" 는 기간 필터가
 *    맡으므로, 쪽 넘기기는 **앞뒤로 넘기는 것**이 주 역할이다 — 가까운 쪽은 한 번에, 먼 쪽은 기간으로.
 * 🚨 **번호를 다섯보다 늘리지 않는다.** 터치 타깃이 `touch`(44)라 화살표까지 일곱 칸이 360px 폭의 한계다.
 *    "…" 과 처음/끝 번호를 더하면 아홉 칸이 되어 넘친다.
 * 🚨 **지금 쪽은 색 하나에 맡기지 않는다** — 채운 원(`brand-soft`) + 굵기 + `aria-current="page"`.
 *    고른 칩(`chip-choice`)과 같은 색이라 새 브랜드 자리를 만들지 않는다.
 * 🚨 **끝에 닿은 화살표는 숨기지 않고 끈다.** 숨기면 번호 줄이 좌우로 밀려서, 누르던 자리에 다른 것이 선다.
 * 🚨 **쪽이 하나면 그리지 않는다.** 갈 데 없는 화살표 둘과 "1" 하나는 군더더기다.
 * ⚠️ 숫자는 `tabular-nums` 다 — 9 에서 10 으로 넘어갈 때 칸 안의 글자 폭이 출렁이지 않게.
 */
export function Pagination({
  page,
  totalPages,
  onChange,
  label,
}: {
  /** 지금 쪽 (1부터). */
  page: number;
  totalPages: number;
  onChange: (page: number) => void;
  /** 줄 전체의 이름 — 스크린리더가 "기록 쪽 넘기기, 탐색" 처럼 읽는다. */
  label: string;
}) {
  if (totalPages <= 1) return null;

  return (
    <nav aria-label={label} className="flex items-center justify-center">
      <IconButton label="이전 쪽" disabled={page <= 1} onClick={() => onChange(page - 1)}>
        <ChevronLeft aria-hidden size={ICON_SIZE.md} strokeWidth={ICON_STROKE} />
      </IconButton>
      <ol className="flex items-center">
        {pageWindow(page, totalPages).map((n) => {
          const current = n === page;
          return (
            <li key={n}>
              <button
                type="button"
                aria-label={`${n}쪽`}
                aria-current={current ? "page" : undefined}
                onClick={() => {
                  if (!current) onChange(n);
                }}
                className={cn(
                  "h-touch text-body-sm ease-standard flex aspect-square items-center justify-center rounded-full tabular-nums transition-colors duration-120",
                  current
                    ? "bg-brand-soft text-brand-ink font-semibold"
                    : "text-ink-muted hover:bg-surface-muted active:bg-surface-muted",
                )}
              >
                {n}
              </button>
            </li>
          );
        })}
      </ol>
      <IconButton label="다음 쪽" disabled={page >= totalPages} onClick={() => onChange(page + 1)}>
        <ChevronRight aria-hidden size={ICON_SIZE.md} strokeWidth={ICON_STROKE} />
      </IconButton>
    </nav>
  );
}

/**
 * 보일 쪽 번호. 지금 쪽을 가운데 두되, 처음이나 끝에 닿으면 창을 그쪽 끝에 붙인다.
 * 🚨 날짜가 아니라 쪽 번호의 더하기다 — apps/web/CLAUDE.md §4 의 날짜 계산 금지와 무관하다.
 */
export function pageWindow(page: number, totalPages: number): number[] {
  const size = Math.min(WINDOW, totalPages);
  const start = Math.min(Math.max(page - Math.floor(size / 2), 1), totalPages - size + 1);
  return Array.from({ length: size }, (_, i) => start + i);
}
