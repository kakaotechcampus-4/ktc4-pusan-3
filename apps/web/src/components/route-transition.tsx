"use client";

import { ViewTransition, type ReactNode, type ViewTransitionClass } from "react";

import { TRANSITION_TYPE } from "@/lib/view-transition";

/**
 * 03 홈 · 04 대화 화면을 통째로 감싸는 전환 (#226 · 디자인 시스템 §8 "허용하는 화면 전환").
 *
 * 두 화면은 **한 쌍**이다 — 대화는 오른쪽에서 들어와 열리고, 닫히면 오른쪽으로 비켜나 홈이 제자리로
 * 돌아온다. 기기의 앞으로 · 뒤로 방향(안드로이드 · iOS 기본 전환)과 같은 쪽이다.
 * 그래서 같은 방향 값(`TRANSITION_TYPE`)에 두 화면이 서로 반대 움직임을 낸다. 실제 움직임(거리 · 시간 ·
 * 곡선)은 `globals.css` 의 `::view-transition-*(.chat-in)` 같은 클래스가 정한다.
 *
 * 🚨 **`default: "none"` 을 지운다면 이 파일을 지울 때다.** 방향 값이 없는 이동(하단 네비 · 기기
 *    뒤로가기 · 새로고침)까지 움직이면 화면 전체가 움직이는 전환이 앱 전체로 번진다 (§8).
 * 🚨 **페이지에서 감싼다. 레이아웃에서 감싸지 않는다** — 레이아웃은 이동에도 남아 있어서
 *    enter · exit 가 안 일어난다 (Next 문서 `view-transitions.md`).
 */
export function ChatRouteTransition({
  side,
  children,
}: {
  /** 어느 쪽 화면인가. */
  side: "home" | "chat";
  children: ReactNode;
}) {
  const motion: { enter: ViewTransitionClass; exit: ViewTransitionClass } =
    side === "chat"
      ? {
          enter: { [TRANSITION_TYPE.chatOpen]: "chat-in", default: "none" },
          exit: { [TRANSITION_TYPE.chatClose]: "chat-out", default: "none" },
        }
      : {
          enter: { [TRANSITION_TYPE.chatClose]: "home-in", default: "none" },
          exit: { [TRANSITION_TYPE.chatOpen]: "home-out", default: "none" },
        };

  return (
    <ViewTransition enter={motion.enter} exit={motion.exit} default="none">
      {children}
    </ViewTransition>
  );
}
