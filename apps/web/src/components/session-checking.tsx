import { Screen } from "@/components/ui/screen";
import { Spinner } from "@/components/ui/spinner";

/**
 * 앱을 켠 뒤 세션을 확인하는 동안의 화면 (refresh → `/me`).
 *
 * 비워 두면 느린 망에서 왕복 두 번 동안 앱이 멈춘 것처럼 보인다. 로그인 화면을 미리 그리지 않는
 * 이유는 그대로다 — 로그인한 보호자에게 로그인 화면이 깜빡였다가 홈으로 넘어간다.
 *
 * 🚨 **문구를 뺄 수 없다.** `prefers-reduced-motion` 에서는 스피너가 숨는다 (디자인 시스템 §8) —
 *    문구가 없으면 빈 화면이 된다. 콜백 화면의 "로그인하는 중…" 과 같은 모양이다.
 */
export function SessionChecking() {
  return (
    <Screen className="justify-center gap-4">
      <p
        className="text-body text-ink-muted flex items-center justify-center gap-2"
        aria-live="polite"
      >
        <Spinner />
        불러오는 중…
      </p>
    </Screen>
  );
}
