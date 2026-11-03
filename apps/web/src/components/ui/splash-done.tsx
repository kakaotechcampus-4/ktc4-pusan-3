"use client";

import { useEffect } from "react";

import { notifyFirstScreen } from "@/lib/native/splash";

/**
 * 첫 화면이 섰다고 앱 셸에 알린다 — 셸이 스플래시를 내린다 (#690 · `lib/native/splash.ts`).
 *
 * `Screen` 이 그린다. `Screen` 은 서버 컴포넌트에서도 쓰여서 효과를 직접 걸 수 없어 따로 뺐다.
 * 앱에서는 셸의 스플래시 덮개가 웹뷰 위에 있고, 알리면 0.4초 동안 흐려지며 걷힌다. 효과는 화면을 커밋한 뒤에
 * 돌아서 걷히는 사이에 이 화면이 그려진다 — 프레임을 따로 기다리지 않는다.
 */
export function SplashDone() {
  useEffect(() => {
    notifyFirstScreen();
  }, []);
  return null;
}
