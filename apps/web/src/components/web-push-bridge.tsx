"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";

import { PUSH_EVENT, readPushEvent, shellPush } from "@/lib/native/push";
import {
  forgetWebPushRegistration,
  pushOpenPath,
  registerShellToken,
  syncWebPush,
} from "@/lib/push/web-push";
import { hasLiveSession, useSessionStore } from "@/stores/session";

/**
 * 웹 푸시와 화면을 잇는다 (#609). 그리는 것은 없다.
 *
 * - **로그인이 살아나면 기기를 등록한다.** 앱을 켤 때(`restore`)와 로그인 직후가 같은 순간이다.
 *   🚨 권한은 여기서 묻지 않는다 — 이미 허용한 브라우저만 등록한다 (`syncWebPush`).
 *   access 갱신은 로그인이 바뀐 것이 아니라 다시 부르지 않는다.
 * - **로그아웃하면 등록한 토큰을 잊는다.** 서버가 기기를 로그인과 함께 지웠으니 다음 로그인에 다시 보낸다.
 * - **알림을 누르면 그 화면으로 옮긴다.** 열린 탭이 있으면 워커가 새 탭 대신 이 탭에 갈 곳을 보낸다
 *   (`public/push-sw.js`). 앱이면 셸이 같은 주소를 `icatch:push` 로 보낸다. 🚨 주소는 모양을 본 것만 받는다.
 * - **앱이면 셸이 넘긴 토큰을 등록한다** (#609 FE-4). 셸에 "준비됐다(`ready`)" 를 알려야 앱을 연 알림의 주소가 온다.
 *
 * 🚨 **목이 뜬 뒤에 선다** (`providers.tsx`). 먼저 서면 등록 요청이 목을 지나쳐 실서버로 간다.
 */
export function WebPushBridge() {
  const router = useRouter();
  const live = useSessionStore(hasLiveSession);

  useEffect(() => {
    if (!live) {
      forgetWebPushRegistration();
      return;
    }
    // 🚨 실패해도 아무 말 하지 않는다 — 알림은 덤이다. 로그에도 남기지 않는다(토큰이 섞일 수 있다).
    syncWebPush().catch(() => {});
  }, [live]);

  useEffect(() => {
    if (typeof navigator === "undefined" || !("serviceWorker" in navigator)) return;
    const container = navigator.serviceWorker;
    const onMessage = (event: MessageEvent) => {
      const path = pushOpenPath(event.data);
      if (path) router.push(path);
    };
    container.addEventListener("message", onMessage);
    return () => container.removeEventListener("message", onMessage);
  }, [router]);

  useEffect(() => {
    const onShellPush = (event: Event) => {
      const message = readPushEvent((event as CustomEvent<unknown>).detail);
      if (message?.type === "token") registerShellToken(message.token).catch(() => {});
      // 🚨 주소의 모양은 `readPushEvent` 가 이미 봤다 (`lib/push/open-path.ts`).
      if (message?.type === "open") router.push(message.path);
    };
    window.addEventListener(PUSH_EVENT, onShellPush);
    // 🚨 듣기 시작한 뒤에 알린다 — 앱을 연 알림의 주소는 이 신호를 받고서야 온다.
    shellPush()?.ready();
    return () => window.removeEventListener(PUSH_EVENT, onShellPush);
  }, [router]);

  return null;
}
