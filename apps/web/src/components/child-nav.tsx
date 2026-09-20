"use client";

import { CalendarDays, House, Notebook, UserRound, type LucideIcon } from "lucide-react";
import Link from "next/link";

import { ICON_SIZE, ICON_STROKE } from "@/components/ui/icon";
import { useChildId } from "@/hooks/use-child-id";
import { cn } from "@/lib/cn";

/**
 * 화면 아래 이동 바 — 홈 · 캘린더 · 기록/기억 · 아이 네 칸.
 *
 * 프로토타입의 3칸 탭에서 출발했다. 다만 아이콘은 유니코드 기호(⌂ ▦ ☰)가 아니라 lucide 다 —
 * 기호를 글자로 찍으면 스크린리더가 제각각 읽고 글리프가 없으면 두부(□)가 된다 (문서 §4).
 *
 * 🚨 **네 번째 칸("아이")은 프로토타입에 없다.** 11 아이 프로필 화면(#74)이 들어오면서
 *    생겼다. 앞의 셋이 "무슨 일이 있었나" 를 보는 곳이라면 이 칸은 **아이 자체**로 간다 —
 *    부르는 이름 · 키·몸무게 · 보호자만 확정하는 안전 정보.
 *    🚨 아이콘은 `Baby` 가 아니라 `UserRound` 다. 이 제품의 대상 연령은 유아이고(만 4~5세),
 *    아기 아이콘은 사실과 다른 데다 "원색 캐릭터 키즈 앱으로 가지 않는다"(문서 §1)와 같은
 *    방향으로 어긋난다. 나머지 셋이 전부 뜻이 좁은 사물이라 여기도 장식 없는 것을 쓴다.
 *
 * 🚨 **여기 있는 셋은 "가는 곳" 이지 "하는 일" 이 아니다.** 04 저장 결과 · 05 제안 후보처럼
 *    흐름 중인 화면에는 붙이지 않는다 — 고르는 도중에 다른 데로 새는 길을 만들면 그 화면이
 *    끝나지 않는다. 그 화면들은 자기 "홈으로" 버튼으로 빠져나간다.
 *
 * 🚨 **설정에서는 홈이 켜진 채로 둔다.** 설정은 홈 헤더에서 들어가는 곁가지라 자기 칸이 없고,
 *    아무 칸도 안 켜진 네비는 고장난 것처럼 보인다 (프로토타입도 같은 처리였다).
 *    다만 그건 **시각적** 결정이라 `onRoute={false}` 로 `aria-current` 만 뺀다 —
 *    켜 보이는 것과 "지금 그 화면에 있다" 는 다른 신호고, 후자를 거짓으로 말하면 안 된다.
 *
 * 🚨 **아이 스코프는 URL 이 정본이다** (apps/web/CLAUDE.md §3). 링크를 `useChildId()` 로 만든다.
 */
export type ChildTab = "home" | "calendar" | "memories" | "profile";

const TABS: Array<{ key: ChildTab; label: string; icon: LucideIcon; path: string }> = [
  { key: "home", label: "홈", icon: House, path: "home" },
  { key: "calendar", label: "캘린더", icon: CalendarDays, path: "calendar" },
  { key: "memories", label: "기록/기억", icon: Notebook, path: "memories" },
  { key: "profile", label: "아이", icon: UserRound, path: "profile" },
];

export function ChildNav({ active, onRoute = true }: { active: ChildTab; onRoute?: boolean }) {
  const childId = useChildId();

  return (
    <nav aria-label="화면 이동">
      {/* 🚨 칸 수를 상수로 세지 않는다 — `grid-cols-4` 는 Tailwind 가 빌드 때 훑는 문자열이라
          `grid-cols-${n}` 으로 만들면 클래스가 생성되지 않는다. TABS 를 늘리면 여기도 고친다. */}
      <ul className="grid grid-cols-4">
        {TABS.map((tab) => {
          const on = tab.key === active;
          const Icon = tab.icon;

          return (
            <li key={tab.key}>
              <Link
                href={`/child/${childId}/${tab.path}`}
                // 🚨 **켜 보이는 것과 "여기 있다" 는 다른 신호다.** 설정에서 홈 칸을 켜 두는 것은
                //    시각적 결정인데(위 주석), 그걸 `aria-current` 로 같이 내보내면 스크린리더가
                //    제목이 "설정" 인 화면에서 "홈, 현재 페이지" 라고 **사실과 다르게** 읽는다.
                aria-current={on && onRoute ? "page" : undefined}
                className={cn(
                  "min-h-touch ease-standard flex flex-col items-center justify-center gap-1 py-1 transition-colors duration-120",
                  // 🚨 켜진 칸의 신호를 **색 하나로 두지 않는다.** `brand` 와 `ink-subtle` 은
                  //    휘도 차가 1.15:1 이라 그레이스케일·적록색약에서 거의 같고, 본문 서체가
                  //    단일 웨이트라 굵기로도 못 만든다 (문서 §3 · §4). 그래서 모양을 하나 더 준다 —
                  //    07 탭이 이미 쓰는 `brand` 2px 선이라 새 언어가 아니다 (문서 §7).
                  //    자리는 늘 차지하게 두께만 깔아 두고 색은 아래 분기가 준다 —
                  //    켜질 때 칸이 밀리면 안 된다. 🚨 `border-transparent` 를 여기 두고
                  //    분기에서 `border-brand` 로 덮으려 하면 **안 덮인다.** `cn()` 은
                  //    tailwind-merge 가 아니고, 둘 다 `border-color` 라 특정도가 같아서
                  //    클래스 순서가 아니라 생성된 CSS 순서가 이긴다 (실제로 투명이 이겼다).
                  "border-t-2",
                  // 🚨 포커스 링을 안쪽에 그린다 — 바의 위쪽 경계선과 겹쳐 잘린다.
                  "focus-visible:-outline-offset-2",
                  on
                    ? "border-brand text-brand"
                    : // 🚨 누른 느낌은 바닥 면(`surface-muted`)보다 **밝은** 쪽으로 뒤집는다.
                      // 같은 muted 로 칠하면 바탕에 묻혀 웹뷰에서 아무 반응이 없어 보인다.
                      // 🚨 **호버는 주지 않는다.** 이 화면은 대부분 웹뷰라 호버가 없고,
                      //    마우스에서만 칸이 밝아지면 커서가 지나갈 때마다 바닥이 들썩인다.
                      //    눌린 피드백은 `active:` 하나로 충분하다.
                      "text-ink-subtle active:text-ink active:bg-surface border-transparent",
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
