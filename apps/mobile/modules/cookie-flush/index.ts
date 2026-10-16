import { requireOptionalNativeModule } from "expo";

/**
 * 웹뷰 쿠키를 디스크에 쓰는 Android 네이티브 모듈 (#469 · `android/` 의 `CookieFlushModule`).
 *
 * iOS 와 Expo Go 에는 없어서 `null` 이다 — 그때는 아무것도 하지 않는다. WKWebView 는 쿠키를
 * 스스로 저장한다.
 */
export const CookieFlush = requireOptionalNativeModule<{ flush(): Promise<void> }>("CookieFlush");
