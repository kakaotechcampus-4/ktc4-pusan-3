"use client";

import { CalendarDays, House, Notebook, type LucideIcon } from "lucide-react";
import Link from "next/link";

import { ICON_SIZE, ICON_STROKE } from "@/components/ui/icon";
import { useChildId } from "@/hooks/use-child-id";
import { cn } from "@/lib/cn";

/**
 * 화면 아래 이동 바 — 홈 · 캘린더 · 기억 세 칸.
 *
 * 프로토타입의 3칸 탭을 그대로 옮겼다. 다만 아이콘은 유니코드 기호(⌂ ▦ ☰)가 아니라 lucide 다 —
 * 기호를 글자로 찍으면 스크린리더가 제각각 읽고 글리프가 없으면 두부(□)가 된다 (문서 §4).
 *
 * 🚨 **여기 있는 셋은 "가는 곳" 이지 "하는 일" 이 아니다.** 04 저장 결과 · 05 제안 후보처럼
 *    흐름 중인 화면에는 붙이지 않는다 — 고르는 도중에 다른 데로 새는 길을 만들면 그 화면이
 *    끝나지 않는다. 그 화면들은 자기 "홈으로" 버튼으로 빠져나간다.
 *
 * 🚨 **설정에서는 홈이 켜진 채로 둔다.** 설정은 홈 헤더에서 들어가는 곁가지라 자기 칸이 없고,
 *    아무 칸도 안 켜진 네비는 고장난 것처럼 보인다 (프로토타입도 같은 처리였다).
 *
 * 🚨 **아이 스코프는 URL 이 정본이다** (apps/web/CLAUDE.md §3). 링크를 `useChildId()` 로 만든다.
 */
export type ChildTab = "home" | "calendar" | "memories";

const TABS: Array<{ key: ChildTab; label: string; icon: LucideIcon; path: string }> = [
  { key: "home", label: "홈", icon: House, path: "home" },
  { key: "calendar", label: "캘린더", icon: CalendarDays, path: "calendar" },
  { key: "memories", label: "기억", icon: Notebook, path: "memories" },
];

export function ChildNav({ active }: { active: ChildTab }) {
  const childId = useChildId();

  return (
    <nav aria-label="화면 이동">
      <ul className="grid grid-cols-3">
        {TABS.map((tab) => {
          const current = tab.key === active;
          const Icon = tab.icon;

          return (
            <li key={tab.key}>
              <Link
                href={`/child/${childId}/${tab.path}`}
                aria-current={current ? "page" : undefined}
                className={cn(
                  "min-h-touch ease-standard flex flex-col items-center justify-center gap-1 py-2 transition-colors duration-120",
                  // 🚨 포커스 링을 안쪽에 그린다 — 바의 위쪽 경계선과 겹쳐 잘린다.
                  "focus-visible:-outline-offset-2",
                  current
                    ? "text-brand"
                    : "text-ink-subtle hover:text-ink active:text-ink hover:bg-surface-muted active:bg-surface-muted",
                )}
              >
                <Icon aria-hidden size={ICON_SIZE.md} strokeWidth={ICON_STROKE} />
                {/* 🚨 라벨을 지우지 않는다. 아이콘만 있는 탭은 무엇인지 외워야 하고,
                    색과 모양이 단독 신호가 되면 안 된다 (문서 §3 · §10). */}
                <span className="text-caption">{tab.label}</span>
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
