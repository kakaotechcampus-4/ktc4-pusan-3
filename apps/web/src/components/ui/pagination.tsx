"use client";

import { ChevronLeft, ChevronRight } from "lucide-react";

import { ICON_SIZE, ICON_STROKE } from "./icon";
import { IconButton } from "./icon-button";
import { Select } from "./select";

/**
 * 디자인 시스템 §7 쪽 넘기기 — 번호 쪽 목록의 아래에 서는 한 줄 (#584 · 07 기록 · 기억).
 *
 * **`[이전] [3쪽 ▾] / 5쪽 [다음]`** 이다. 화살표 둘은 `IconButton`, 가운데는 `Select` 다 —
 * 새 치수를 만들지 않고 있는 부품 셋을 세운다.
 *
 * 🚨 **번호 버튼을 늘어놓지 않는다.** 터치 타깃이 44px 이라 360px 폭에서는 화살표까지 일곱 칸이
 *    한계고, 그러면 가운데 쪽은 화살표로만 가게 된다. 고르기 상자는 어느 쪽이든 한 번에 간다.
 * 🚨 **쪽이 하나뿐이면 그리지 않는다.** 갈 데가 없는 화살표 둘과 "1쪽 / 1쪽" 은 군더더기다.
 * 🚨 **끝에 닿은 화살표는 숨기지 않고 끈다.** 숨기면 가운데 상자가 좌우로 밀려서, 누르던 자리에
 *    다른 것이 선다.
 *
 * ⚠️ 쪽 상자는 `labelHidden` 이다 — 값("3쪽")이 단위를 지고, 무엇을 고르는 줄인지는 바깥 `nav` 의
 *    이름이 진다 (`Select` 머리말의 `labelHidden` 조건).
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

  const options = Array.from({ length: totalPages }, (_, index) => ({
    value: String(index + 1),
    label: `${index + 1}쪽`,
  }));

  return (
    <nav aria-label={label} className="flex items-center justify-center gap-2">
      <IconButton label="이전 쪽" disabled={page <= 1} onClick={() => onChange(page - 1)}>
        <ChevronLeft aria-hidden size={ICON_SIZE.md} strokeWidth={ICON_STROKE} />
      </IconButton>
      {/* `Select` 루트가 `flex-1` 이라 폭을 여기서 정한다. "10000쪽" 까지 한 줄에 든다. */}
      <div className="flex w-28">
        <Select
          label="쪽 고르기"
          labelHidden
          value={String(page)}
          options={options}
          onChange={(value) => onChange(Number(value))}
        />
      </div>
      <span className="text-body-sm text-ink-muted whitespace-nowrap">/ {totalPages}쪽</span>
      <IconButton label="다음 쪽" disabled={page >= totalPages} onClick={() => onChange(page + 1)}>
        <ChevronRight aria-hidden size={ICON_SIZE.md} strokeWidth={ICON_STROKE} />
      </IconButton>
    </nav>
  );
}
