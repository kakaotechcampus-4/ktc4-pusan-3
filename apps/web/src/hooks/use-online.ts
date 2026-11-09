"use client";

import { useSyncExternalStore } from "react";

function subscribe(onChange: () => void): () => void {
  window.addEventListener("online", onChange);
  window.addEventListener("offline", onChange);
  return () => {
    window.removeEventListener("online", onChange);
    window.removeEventListener("offline", onChange);
  };
}

const readOnline = () => navigator.onLine;
/** 서버 렌더에서는 모른다 — 끊겼다고 말하지 않는 쪽으로 둔다. */
const serverOnline = () => true;

/**
 * 기기가 네트워크에 붙어 있는가 (`navigator.onLine`).
 *
 * ⚠️ `true` 가 "서버에 닿는다" 는 뜻은 아니다 — 와이파이에 붙었는데 인터넷이 안 되는 경우도 `true` 다.
 *    `false` 만 믿을 수 있다 (끊긴 것은 확실하다). 그래서 이 값으로는 **막기만** 하고, 닿는지는 보내 봐야 안다.
 */
export function useOnline(): boolean {
  return useSyncExternalStore(subscribe, readOnline, serverOnline);
}
