/**
 * 이 앱은 apps/web 을 띄우는 웹뷰 껍데기다 (CLAUDE.md §7 "모바일: React Native 웹뷰").
 * 화면·상태·API 호출은 전부 web 에 있고, 여기는 네이티브만 할 수 있는 것을 맡는다.
 */

import { isInternalUrlOf } from "./native/internal-url";

const rawUrl = process.env.EXPO_PUBLIC_WEB_URL;

if (!rawUrl) {
  throw new Error(
    "EXPO_PUBLIC_WEB_URL 이 없다. .env.example 을 .env.local 로 복사할 것 (apps/mobile/README.md 참고).",
  );
}

export const WEB_URL = rawUrl.replace(/\/$/, "");

/** 웹뷰 안에 머물러도 되는 출처. 여기 밖은 앱 밖(in-app 브라우저 · 인증 세션 · OS)으로 넘긴다. */
export const ALLOWED_ORIGIN = new URL(WEB_URL).origin;

/** 출처가 같아도 API 경로(`/api/…`)는 웹뷰 밖이다 (`src/native/internal-url.ts`). */
export function isInternalUrl(url: string): boolean {
  return isInternalUrlOf(url, ALLOWED_ORIGIN);
}
