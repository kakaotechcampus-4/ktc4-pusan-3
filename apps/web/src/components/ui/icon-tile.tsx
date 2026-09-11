import type { LucideIcon } from "lucide-react";

import { cn } from "@/lib/cn";

import { ICON_SIZE, ICON_STROKE } from "./icon";

/**
 * 디자인 시스템 §7 아이콘 타일 — `brand-soft` 바탕에 아이콘 하나.
 *
 * 목록 줄 앞에 서서 **어떤 종류의 줄인지**를 모양으로 알린다. 이 서비스는 색을 아껴 쓰기로
 * 정해서 화면이 쉽게 밋밋해지는데, 브랜드색을 넓게 칠하는 대신 이 작은 타일로 들여보낸다.
 *
 * 🚨 **도메인 색(`food`·`activity`·`education`·`health`)을 여기에 넣지 않는다.**
 *    도메인 색은 "이 추천이 어느 Agent 에서 왔는가" 신호이고(문서 §3), 사실을 나열하는 줄에
 *    쓰면 그 뜻이 흐려진다. 도메인을 보여야 하는 자리는 `DomainChip` 이다.
 *
 * 🚨 아이콘은 `aria-hidden` 이다 — 뜻은 옆의 글자가 진다.
 */
export function IconTile({ icon: Icon, className }: { icon: LucideIcon; className?: string }) {
  return (
    <span
      className={cn(
        "bg-brand-soft text-brand-ink rounded-field flex size-9 shrink-0 items-center justify-center",
        className,
      )}
    >
      <Icon aria-hidden size={ICON_SIZE.md} strokeWidth={ICON_STROKE} />
    </span>
  );
}
