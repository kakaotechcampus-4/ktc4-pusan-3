"use client";

import { ViewTransition, type ReactNode } from "react";

import { TRANSITION_TYPE } from "@/lib/view-transition";

/**
 * 화면 하나를 통째로 감싸는 전환 (디자인 시스템 §8 "허용하는 화면 전환"). 03 홈 ↔ 04 대화(#226)에서 시작했다.
 *
 * 쌍을 이루는 두 화면이 **같은 것**으로 감싼다 — 자식은 오른쪽에서 들어와 열리고, 돌아가면 오른쪽으로
 * 비켜나 부모가 제자리로 돌아온다. 기기의 앞으로 · 뒤로 방향(안드로이드 · iOS 기본 전환)과 같은 쪽이다.
 * 실제 움직임(거리 · 시간 · 곡선)은 `globals.css` 의 `::view-transition-*(.screen-forward-in)` 같은 클래스가 정한다.
 *
 * 🚨 **`default: "none"` 을 지운다면 이 파일을 지울 때다.** 방향 값이 없는 이동(하단 네비 · 기기
 *    뒤로가기 · 새로고침)까지 움직이면 화면 전체가 움직이는 전환이 앱 전체로 번진다 (§8).
 * 🚨 **페이지에서 감싼다. 레이아웃에서 감싸지 않는다** — 레이아웃은 이동에도 남아 있어서
 *    enter · exit 가 안 일어난다 (Next 문서 `view-transitions.md`).
 * 🚨 감싸기만 해서는 아무것도 안 움직인다. 이동하는 쪽이 방향(`FORWARD` · `BACK`)을 붙여야 한다.
 */
export function ScreenTransition({ children }: { children: ReactNode }) {
  return (
    <ViewTransition
      enter={{
        [TRANSITION_TYPE.forward]: "screen-forward-in",
        [TRANSITION_TYPE.back]: "screen-back-in",
        default: "none",
      }}
      exit={{
        [TRANSITION_TYPE.forward]: "screen-forward-out",
        [TRANSITION_TYPE.back]: "screen-back-out",
        default: "none",
      }}
      default="none"
    >
      {children}
    </ViewTransition>
  );
}
