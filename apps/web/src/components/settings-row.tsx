import Link from "next/link";
import { ChevronRight, type LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

import { ICON_SIZE, ICON_STROKE } from "@/components/ui/icon";
import { IconTile } from "@/components/ui/icon-tile";
import { cn } from "@/lib/cn";

/**
 * 10 설정의 줄 하나. 왼쪽 아이콘 타일 · 가운데 이름과 **지금 상태** · 오른쪽 행동.
 *
 * 🚨 **상태를 글자로 단다.** 이 화면의 요지가 "지금 어떻게 되어 있는가" 라서, 누르기 전에
 *    읽히는 값이 없으면 구조가 무너진다 (방향 계약 THESIS). 아이콘·색으로 대신하지 않는다.
 *
 * 🚨 **줄을 카드로 감싸지 않는다.** 구역이 셋이라 줄마다 상자를 두르면 화면이 상자의 나열이
 *    된다 — 여기서는 `surface` 한 덩어리 안에 `line` 1px 구분선으로 줄을 나눈다
 *    (디자인 시스템 §7 "카드 속 카드를 만들지 않는다").
 */
export function SettingsGroup({ children }: { children: ReactNode }) {
  return (
    <ul className="rounded-card bg-surface border-line divide-line divide-y border">{children}</ul>
  );
}

function RowBody({
  icon,
  title,
  onTitleClick,
  status,
  note,
  trailing,
}: {
  icon: LucideIcon;
  title: ReactNode;
  onTitleClick?: () => void;
  status?: ReactNode;
  note?: ReactNode;
  trailing?: ReactNode;
}) {
  return (
    <>
      <IconTile icon={icon} />
      <span className="min-w-0 flex-1">
        {onTitleClick ? (
          /* 🚨 **줄의 이름이 읽으러 가는 길이다.** 오른쪽에 버튼을 하나 더 쌓으면 줄이
             내용보다 길어지고 오른쪽 열이 어떤 줄은 하나, 어떤 줄은 둘이 된다 —
             구역 머리줄로 올려 걷어냈던 그 실루엣이 목록 안에서 다시 생긴다.
             🚨 밑줄을 지우지 않는다. 이 시스템에는 누를 수 있는 글자가 거의 없어서
             표시가 없으면 아무도 안 누른다 (색은 단독 신호가 될 수 없다 · 문서 §3). */
          <button
            type="button"
            onClick={onTitleClick}
            className="text-body text-ink ease-standard decoration-line-strong hover:decoration-ink-muted active:text-ink-muted -my-2.5 block max-w-full py-2.5 text-left underline decoration-1 underline-offset-4 transition-colors duration-120 focus-visible:-outline-offset-2"
          >
            {title}
          </button>
        ) : (
          <span className="text-body text-ink block">{title}</span>
        )}
        {status ? <span className="text-body-sm text-ink-muted mt-0.5 block">{status}</span> : null}
        {note ? <span className="text-caption text-ink-subtle mt-1 block">{note}</span> : null}
      </span>
      {trailing}
    </>
  );
}

/** 다른 화면으로 가는 줄. 🚨 줄 전체가 링크다 — 쉐브론만 누르게 만들지 않는다. */
export function SettingsLinkRow({
  href,
  icon,
  title,
  status,
  note,
}: {
  href: string;
  icon: LucideIcon;
  title: ReactNode;
  status?: ReactNode;
  note?: ReactNode;
}) {
  return (
    <li>
      <Link
        href={href}
        className={cn(
          "min-h-touch ease-standard flex w-full items-center gap-3 p-4 text-left transition-colors duration-120",
          // 🚨 웹뷰에는 호버가 없다. 누른 느낌을 `active:` 로 반드시 준다 (문서 §8).
          "hover:bg-surface-muted active:bg-surface-muted",
          "focus-visible:-outline-offset-2",
        )}
      >
        <RowBody
          icon={icon}
          title={title}
          status={status}
          note={note}
          trailing={
            <ChevronRight
              aria-hidden
              size={ICON_SIZE.md}
              strokeWidth={ICON_STROKE}
              className="text-ink-subtle shrink-0"
            />
          }
        />
      </Link>
    </li>
  );
}

/**
 * 줄 자체는 정보고, 행동은 오른쪽 버튼이 따로 가져가는 줄.
 *
 * 🚨 **줄 전체를 버튼으로 만들지 않는다.** 여기 달리는 행동은 철회·연결 끊기처럼 되돌리기
 *    어려운 것이라, 줄 어디를 눌러도 실행되면 잘못 누르는 경로가 생긴다.
 */
export function SettingsInfoRow({
  icon,
  title,
  onTitleClick,
  status,
  note,
  action,
}: {
  icon: LucideIcon;
  title: ReactNode;
  /** 이름을 누르면 여는 것. 읽기만 하는 자리에 쓴다 (동의 전문). */
  onTitleClick?: () => void;
  status?: ReactNode;
  note?: ReactNode;
  /**
   * 🚨 **한 줄에 버튼은 하나까지다.** 두 개를 쌓으면 줄 높이가 내용의 두 배가 되고 왼쪽에
   * 빈 띠가 생기며, 오른쪽 열이 줄마다 하나와 둘을 오간다. 읽으러 가는 길이 더 필요하면
   * `onTitleClick` 으로 이름에 건다.
   */
  action?: ReactNode;
}) {
  return (
    <li className="flex items-center gap-3 p-4">
      <RowBody
        icon={icon}
        title={title}
        onTitleClick={onTitleClick}
        status={status}
        note={note}
        trailing={action}
      />
    </li>
  );
}
