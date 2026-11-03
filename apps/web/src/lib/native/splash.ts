/**
 * 앱 셸의 스플래시 창구 — 웹 쪽 약속 (#690).
 *
 * 셸은 앱을 켤 때 뜬 스플래시를 **웹의 첫 화면이 설 때까지** 들고 있다. 문서가 뜬 것만으로 내리면 로그인한
 * 보호자는 스플래시 → "불러오는 중…"(세션 확인) → 홈으로 로딩 모양이 한 번 더 바뀐다.
 * 그래서 첫 화면이 서면 웹이 `done` 을 보낸다. 셸 쪽 짝은 `apps/mobile/src/native/splash.ts`.
 *
 * 🚨 보내는 곳은 공통 `Screen` 하나다 (`components/ui/splash-done.tsx`). 기다리는 화면(`SessionChecking`)은
 *    `waiting` 으로 빠진다. 화면마다 부르지 않는다 — 하나를 빠뜨리면 그 화면에서 켤 때 스플래시가 셸의
 *    시간 초과까지 남는다.
 * 🚨 창구가 없으면(브라우저 · 이 기능 전의 앱) 아무것도 하지 않는다.
 */

/** 셸이 `window.icatch.splash` 에 꽂는 것. */
export interface SplashBridge {
  /** 첫 화면이 섰다. 셸이 스플래시를 내린다. */
  done(): void;
}

declare global {
  interface IcatchShell {
    splash?: SplashBridge;
  }
}

/** 이 문서에서 이미 알렸는가. 화면을 옮길 때마다 `Screen` 이 새로 서지만 셸에 보낼 것은 처음 한 번이다. */
let sent = false;

/** 첫 화면이 섰다고 셸에 알린다. 문서마다 한 번만 보낸다. */
export function notifyFirstScreen(): void {
  if (sent || typeof window === "undefined") return;
  const bridge = window.icatch?.splash;
  if (!bridge) return;
  sent = true;
  bridge.done();
}

/** 테스트 전용 — 모듈 상태를 되돌린다. */
export function resetFirstScreenForTest(): void {
  sent = false;
}
