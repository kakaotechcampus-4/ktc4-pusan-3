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
 *
 * `tone="neutral"` 은 **브랜드색도 도메인색도 쓸 수 없는 목록**을 위한 자리다 (07 기록·기억).
 * 거기서는 색이 아니라 배치가 종류를 말해야 하는데, 타일 자체를 빼면 줄이 글자 세 덩이로만
 * 남아 훑을 기준선이 없어진다. 🚨 뉴트럴 타일을 "밋밋해서" 브랜드로 바꾸지 말 것 — 06 줄마다
 * 초록 타일이 서면 브랜드가 장식이 되고, 안에 든 것이 도메인 아이콘이라 뜻도 섞인다.
 */
export function IconTile({
  icon: Icon,
  tone = "brand",
  className,
}: {
  icon: LucideIcon;
  /**
   * `plain` 은 **색을 바깥에서 주는** 자리다 — 도메인 색은 primitive 가 알면 안 되는 값이라
   * (`components/ui/` 는 토큰만 안다) 호출자가 `className` 으로 `domainField()` 를 넘긴다.
   * 🚨 `cn()` 은 tailwind-merge 가 아니라서, 여기서 `bg-*` 를 깔아 두면 바깥 색이 안 덮인다.
   */
  tone?: "brand" | "neutral" | "plain";
  className?: string;
}) {
  return (
    <span
      className={cn(
        "rounded-field flex size-9 shrink-0 items-center justify-center",
        tone === "plain"
          ? null
          : tone === "neutral"
            ? "bg-surface-muted text-ink-muted"
            : "bg-brand-soft text-brand-ink",
        className,
      )}
    >
      <Icon aria-hidden size={ICON_SIZE.md} strokeWidth={ICON_STROKE} />
    </span>
  );
}
