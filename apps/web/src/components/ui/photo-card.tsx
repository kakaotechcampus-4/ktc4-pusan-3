import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

import { cn } from "@/lib/cn";

import { ICON_STROKE } from "./icon";

/**
 * 디자인 시스템 §7 `card-photo` (08 사진으로 적기).
 *
 * 카드 하나가 **사진과 그 사진에서 읽어낸 것**을 함께 든다. 둘을 따로 세우면 "이 글자는 이
 * 사진에서 나왔다" 가 배치가 아니라 문구로만 남는데, 이 화면이 부모에게 묻는 것이 바로 그
 * 대조라서(`.impeccable/surfaces/…photos….md` THESIS) 한 덩어리여야 한다.
 *
 * 🚨 **`accent`(brand 1px)는 이 화면에서 이 카드 한 장만 쓴다** (문서 §7 — 한 화면에 한 장).
 *    04 저장 결과가 "적어주신 한 줄" 에 같은 테두리를 준 자리와 짝이다. 승인 버튼은 그 아래
 *    "이렇게 저장할게요" 카드가 들고, 그쪽은 화면의 유일한 primary 버튼으로 무게를 가진다.
 *
 * 🚨 **비율은 4:3 고정이다.** 사진마다 높이가 달라지면 아래 내용이 매번 다른 자리에서 시작해서,
 *    사진을 갈아 끼울 때마다 부모가 화면을 다시 찾아야 한다. 잘리는 것은 `object-cover` 가
 *    맡고, 원본이 필요한 판단은 이 화면에 없다 (부모가 방금 찍은 사진이다).
 *
 * 🚨 **점선 테두리를 쓰지 않는다.** 점선은 `card-general`("또래 기준 일반 추천")과
 *    `chip-evidence-stale`("오래된 기록") 이 이미 가져간 뜻이다 — 드롭존으로 쓰면 한 모양이
 *    세 가지를 말하게 된다.
 */
export function PhotoCard({
  children,
  footer,
  tone = "default",
}: {
  /** 4:3 칸 안에 들어가는 것. 사진 `<img>` 또는 사진을 고르는 버튼. */
  children: ReactNode;
  /** 사진 아래에 `line` 1px 로 이어 붙는 것. 없으면 사진만 선다. */
  footer?: ReactNode;
  tone?: "default" | "accent";
}) {
  return (
    <div
      className={cn(
        "rounded-card bg-surface overflow-hidden border",
        tone === "accent" ? "border-brand" : "border-line",
      )}
    >
      {/* 🚨 `overflow-hidden` 이 카드에 걸려 있어서 사진 모서리가 카드 곡률을 따라간다 —
          사진에 따로 radius 를 주면 두 곡률이 1px 어긋나 테두리 안쪽에 실금이 보인다. */}
      <div className="bg-surface-muted aspect-[4/3] w-full">{children}</div>
      {footer ? <div className="border-line border-t p-4">{footer}</div> : null}
    </div>
  );
}

/**
 * 아직 사진이 없을 때 4:3 칸을 채우는 버튼.
 *
 * 🚨 `<label>` + 숨긴 `<input type="file">` 이 아니라 **버튼**이다. 라벨을 누르는 방식은
 *    스크린리더가 파일 입력을 따로 읽어서 같은 것이 두 번 나오고, 눌린 상태(`:active`)가
 *    라벨에 걸리지 않아 웹뷰에서 반응이 없다 (apps/web/CLAUDE.md §5). 입력은 화면 밖에 두고
 *    이 버튼이 `click()` 으로 연다.
 */
export function PhotoSlotButton({
  icon: Icon,
  label,
  hint,
  onClick,
  disabled,
}: {
  icon: LucideIcon;
  label: string;
  /** 무엇을 넣으면 되는지 한 줄. 라벨만으로 부족한 자리다. */
  hint?: string;
  onClick: () => void;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className={cn(
        "ease-standard flex h-full w-full flex-col items-center justify-center gap-2 px-4 text-center transition-colors duration-120",
        disabled ? "text-ink-subtle" : "text-ink-muted hover:bg-line active:bg-line cursor-pointer",
      )}
    >
      {/* 빈 화면에서 유일한 색이다 — `EmptyState` 와 같은 처리다. 아이콘까지 회색이면
          "시작하는 자리" 가 아니라 "비활성" 으로 읽힌다. */}
      <Icon
        aria-hidden
        size={32}
        strokeWidth={ICON_STROKE}
        className={disabled ? "text-ink-subtle" : "text-brand"}
      />
      <span className="text-section">{label}</span>
      {hint ? <span className="text-body-sm text-ink-subtle">{hint}</span> : null}
    </button>
  );
}
