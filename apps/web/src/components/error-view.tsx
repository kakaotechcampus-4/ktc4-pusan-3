import type { ReactNode } from "react";

import { PageTitle } from "@/components/ui/page-title";
import { Screen } from "@/components/ui/screen";

/**
 * 화면 단위로 떨어지는 실패의 본문. `error` · `global-error` · `not-found` 가 같이 쓴다.
 *
 * 🚨 **빨강을 쓰지 않는다.** `danger` 는 알레르기·건강 중단, `caution` 은 승인 게이트 2곳
 *    전용이다 (디자인 시스템 §3). 화면이 안 그려진 것은 그 둘 중 어느 것도 아니다.
 * 🚨 **`EmptyState` 로 그리지 않는다.** 그건 "쌓인 기록 건수" 를 보여주는 자리고,
 *    여기서는 셀 기록이 없다 — 0건을 그리면 기록이 사라진 것처럼 읽힌다.
 * 🚨 **에러 문구를 그대로 띄우지 않는다.** 문구는 화면이 만든다 (`lib/report-render-error.ts`).
 *    `digest` 만 예외인데, 그건 메시지가 아니라 서버 로그와 맞춰 보는 번호다.
 *
 * 사과문이 아니라 **다음 행동**이 본체라서 `action` 을 받는다 — 되돌아갈 길 없이
 * "문제가 생겼어요" 만 있는 화면을 만들지 않는다.
 */
export function ErrorView({
  title,
  description,
  action,
  digest,
}: {
  /**
   * 한 줄 문장이다. 🚨 `<br />` 로 끊지 않는다 — 화면 제목을 끊지 않는 규칙(디자인 시스템 §4)이
   * 여기도 같다. 그래서 문자열만 받는다. 좁은 폭에서 넘치면 `keep-all` 이 어절에서 끊는다.
   * ⚠️ 한동안 "없는 / 주소예요" 처럼 두 줄로 끊어 두었다 — 한 줄에 들어가는 제목이 세로를 한 줄 더 먹었다.
   */
  title: string;
  description: ReactNode;
  action: ReactNode;
  /** 서버에서 난 예외에만 있다. 없으면 줄 자체를 그리지 않는다. */
  digest?: string;
}) {
  return (
    <Screen className="justify-center gap-6">
      <div>
        <PageTitle>{title}</PageTitle>
        <p className="text-body text-ink-muted mt-3">{description}</p>
      </div>

      <div className="flex flex-col gap-2">{action}</div>

      {digest ? <p className="text-caption text-ink-subtle">오류 번호 {digest}</p> : null}
    </Screen>
  );
}
