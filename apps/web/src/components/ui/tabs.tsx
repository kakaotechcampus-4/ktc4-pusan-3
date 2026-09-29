"use client";

import Link from "next/link";

import { DomainIcon } from "@/components/ui/icon";
import type { Agent } from "@/lib/api/types";
import { cn } from "@/lib/cn";

/**
 * 디자인 시스템 §7 탭 (07 화면 2계층).
 *
 * 🚨 **탭 전환은 URL 에 남긴다.** 셋이 다른 엔드포인트라 뒤로가기가 동작해야 한다 (문서 §7).
 *    그래서 버튼이 아니라 `<Link>` 이고, 마크업도 ARIA tablist 가 아니라 **탐색**이다 —
 *    tablist 는 "같은 화면 안에서 패널만 바뀐다" 는 약속인데 여기서는 주소가 바뀐다.
 *    현재 위치는 `aria-current="page"` 가 진다.
 *
 * 🚨 **활성 신호를 굵기 하나에 걸지 않는다.** 문서 표의 "label 600" 은 §4 가 가변폰트로
 *    바뀐 뒤 실제로 굵게 나오지만, 13px 손글씨에서 500 과 600 의 차이는 활성/비활성을
 *    혼자 나르기엔 약하다. 실제로 구분을 만드는 것은 **글자색(`ink` vs `ink-muted`) +
 *    아래 `brand` 2px** 둘이다.
 *
 * 밑줄은 비활성에도 투명으로 깔아 둔다 — 켜질 때 칸이 밀리면 안 된다.
 * 🚨 `border-transparent` 를 앞에 두고 분기에서 덮으려 하면 **안 덮인다.** `cn()` 은
 *    tailwind-merge 가 아니고 둘 다 `border-color` 라 특정도가 같다 (`ChildNav` 에서 겪었다).
 */
export interface TabItem {
  key: string;
  label: string;
  href: string;
}

export function Tabs({
  items,
  active,
  label,
}: {
  items: readonly TabItem[];
  active: string;
  /** 무엇을 고르는 탭인지. 화면에 여러 벌이 생기면 스크린리더가 구분하지 못한다. */
  label: string;
}) {
  return (
    <nav aria-label={label} className="border-line border-b">
      <ul className="flex">
        {items.map((item) => {
          const on = item.key === active;

          return (
            <li key={item.key} className="flex-1">
              <Link
                href={item.href}
                aria-current={on ? "page" : undefined}
                // 🚨 스크롤 위치를 유지한다. 목록을 보다가 탭을 바꾸면 Next 가 기본으로
                //    맨 위로 올리는데, 탭 줄이 화면 위쪽이라 부모가 방금 보던 자리를 잃는다.
                scroll={false}
                className={cn(
                  "text-label min-h-touch ease-standard flex items-center justify-center px-2 text-center transition-colors duration-120",
                  // 밑줄 자리를 늘 차지하게 두께만 깔고 색은 아래 분기가 준다.
                  "-mb-px border-b-2",
                  // 포커스 링이 아래 경계선과 겹쳐 잘린다.
                  "focus-visible:-outline-offset-2",
                  on
                    ? "border-brand text-ink"
                    : "text-ink-muted active:text-ink active:bg-surface-muted border-transparent",
                )}
              >
                {item.label}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}

/**
 * 같은 화면 안에서 **패널만 바꾸는** 탭 (05 제안 후보의 Agent 묶음).
 *
 * 🚨 **위의 `Tabs` 와 마크업이 다르다.** 그쪽은 셋이 다른 주소라 `<Link>` + 탐색이고, 이쪽은
 *    **주소가 그대로**라 ARIA `tablist` 다 — tablist 는 "같은 화면에서 패널만 바뀐다" 는 약속이라
 *    주소가 바뀌는 자리에 쓰면 거짓말이 되고, 반대로 여기서 `<Link>` 를 쓰면 흐름 중인 화면에
 *    **탭을 누를 때마다 히스토리가 하나씩 쌓인다**(뒤로가기가 화면을 못 벗어난다).
 *
 * 🚨 **직접 만드는 대신 접근성이 전부 이 파일의 책임이다** (apps/web/CLAUDE.md §3):
 *    `tablist`/`tab`/`tabpanel` 한 쌍 · `aria-selected` · `aria-controls` ·
 *    **roving tabindex**(꺼진 탭은 Tab 키 순회에서 빠진다) · ←/→ · Home/End.
 * 🚨 **방향키는 고르기까지 한다**(automatic activation). 탭이 둘뿐이고 패널이 가벼워서,
 *    포커스만 옮기고 Enter 를 한 번 더 누르게 하는 쪽이 키보드 사용자에게 손해다.
 *
 * 🚨 **모양은 위 `Tabs` 와 같다.** 한 제품에 탭이 두 가지로 보이면 안 된다 — 활성 신호도
 *    글자색 + 아래 `brand` 2px 둘이다 (13px 손글씨에서 500 과 600 의 차이는 혼자
 *    활성/비활성을 나르기엔 약하다 · 문서 §4).
 */
export interface PanelTabItem {
  key: string;
  label: string;
  /** 왼쪽에 세울 도메인 아이콘. 🚨 `aria-hidden` 이라 뜻은 옆의 라벨이 진다 (문서 §3). */
  icon?: Agent;
}

export function PanelTabs({
  items,
  active,
  onChange,
  label,
  panelId,
}: {
  items: readonly PanelTabItem[];
  active: string;
  onChange: (key: string) => void;
  /** 무엇을 고르는 탭인지. 화면에 여러 벌이 생기면 스크린리더가 구분하지 못한다. */
  label: string;
  /** 이 탭들이 바꾸는 패널의 `id`. 🚨 패널에는 `role="tabpanel"` 과 `aria-labelledby` 를 단다. */
  panelId: string;
}) {
  function move(from: number, to: number) {
    const next = items[Math.min(items.length - 1, Math.max(0, to))];
    if (!next || next.key === items[from]?.key) return;
    onChange(next.key);
    // 🚨 고른 탭으로 포커스를 옮긴다 — roving tabindex 라 꺼진 탭은 포커스를 받을 수 없다.
    document.getElementById(tabId(panelId, next.key))?.focus();
  }

  return (
    <div role="tablist" aria-label={label} className="border-line flex border-b">
      {items.map((item, index) => {
        const on = item.key === active;

        return (
          <button
            key={item.key}
            id={tabId(panelId, item.key)}
            type="button"
            role="tab"
            aria-selected={on}
            aria-controls={panelId}
            tabIndex={on ? 0 : -1}
            onClick={() => onChange(item.key)}
            onKeyDown={(event) => {
              if (event.key === "ArrowRight") {
                event.preventDefault();
                move(index, index + 1);
              } else if (event.key === "ArrowLeft") {
                event.preventDefault();
                move(index, index - 1);
              } else if (event.key === "Home") {
                event.preventDefault();
                move(index, 0);
              } else if (event.key === "End") {
                event.preventDefault();
                move(index, items.length - 1);
              }
            }}
            className={cn(
              "text-label min-h-touch ease-standard flex flex-1 items-center justify-center gap-1.5 px-2 text-center transition-colors duration-120",
              "-mb-px border-b-2",
              "focus-visible:-outline-offset-2",
              on
                ? "border-brand text-ink"
                : "text-ink-muted hover:text-ink active:text-ink active:bg-surface-muted border-transparent",
            )}
          >
            {item.icon ? <DomainIcon agent={item.icon} size="sm" /> : null}
            {item.label}
          </button>
        );
      })}
    </div>
  );
}

function tabId(panelId: string, key: string): string {
  return `${panelId}-tab-${key}`;
}
