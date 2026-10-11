"use client";

import { useEffect, useState } from "react";

import { toSeoulDateKey } from "@/lib/format";

/**
 * 지금 한국 시간의 날 (`YYYY-MM-DD`). **화면을 켜 둔 채 자정을 넘기면 따라 바뀐다** (#231 리뷰).
 *
 * 🚨 처음 그릴 때 한 번만 계산하면, 켜 둔 대화 화면이 자정 뒤에도 어제를 "오늘" 로 부른다. 그래서 1분마다,
 *    그리고 앱이 다시 앞으로 올 때(`visibilitychange`) 다시 본다 — 웹뷰는 뒤에 있는 동안 타이머를 멈춘다.
 * ⚠️ 자정까지 남은 시간을 계산하지 않는다 — 1분 늦게 바뀌는 것은 괜찮고, 시간대 계산을 한 벌 더 두는 것은
 *    괜찮지 않다 (`lib/format.ts` 의 `toSeoulDateKey` 하나만 쓴다).
 */
export function useSeoulToday(): string {
  const [today, setToday] = useState(() => toSeoulDateKey(new Date()));

  useEffect(() => {
    const refresh = () => setToday(toSeoulDateKey(new Date()));
    const timer = setInterval(refresh, 60_000);
    document.addEventListener("visibilitychange", refresh);
    return () => {
      clearInterval(timer);
      document.removeEventListener("visibilitychange", refresh);
    };
  }, []);

  return today;
}
