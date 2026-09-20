"use client";

import Link from "next/link";

import { cn } from "@/lib/cn";

/**
 * 디자인 시스템 §7 탭 (07 화면 2계층).
 *
 * 🚨 **탭 전환은 URL 에 남긴다.** 셋이 다른 엔드포인트라 뒤로가기가 동작해야 한다 (문서 §7).
 *    그래서 버튼이 아니라 `<Link>` 이고, 마크업도 ARIA tablist 가 아니라 **탐색**이다 —
 *    tablist 는 "같은 화면 안에서 패널만 바뀐다" 는 약속인데 여기서는 주소가 바뀐다.
 *    현재 위치는 `aria-current="page"` 가 진다.
 *
 * 🚨 **활성 신호를 굵기에 걸지 않는다.** 본문 서체가 단일 웨이트라 `label`(500)과 600 이
 *    화면에서 똑같이 나온다 (문서 §4). 문서 표의 "label 600" 은 선언일 뿐이고, 실제로
 *    구분을 만드는 것은 **글자색(`ink` vs `ink-muted`) + 아래 `brand` 2px** 둘이다.
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
