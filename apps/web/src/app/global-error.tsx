"use client";

import { useEffect } from "react";

import { ErrorView } from "@/components/error-view";
import { Button } from "@/components/ui/button";
import { reportRenderError } from "@/lib/report-render-error";

import "./globals.css";

/**
 * 루트 레이아웃이 터졌을 때의 마지막 그물. `app/error.tsx` 는 그 레이아웃 **안**에 있어서
 * 자기를 감싼 레이아웃의 예외를 못 잡는다.
 *
 * 🚨 **이 컴포넌트는 루트 레이아웃을 대체한다.** 그래서
 *    ① `<html>` · `<body>` 를 직접 쓴다 (안 쓰면 문서가 비어 아무것도 안 보인다),
 *    ② `globals.css` 를 여기서 직접 싣는다 — 안 실으면 토큰과 서체 92개가 통째로 빠져
 *       기기 기본 폰트에 스타일 없는 화면이 뜬다,
 *    ③ **Providers 가 없다.** TanStack Query · 토스트 · 세션 스토어를 쓰는 것을
 *       여기 넣지 않는다 (`ErrorView` 와 `Button` 은 둘 다 그런 것을 안 쓴다).
 *
 * 🚨 되돌아가는 길은 `next/link` 가 아니라 **통짜 새로고침**이다. 라우터가 살아 있다는
 *    보장이 없는 상황이라, 링크로 옮기면 같은 깨진 트리를 다시 그린다.
 *
 * ⚠️ 개발 서버에서는 에러 오버레이가 덮는다. `pnpm build && pnpm start` 로 본다.
 */
export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => reportRenderError(error), [error]);

  return (
    <html lang="ko">
      <body className="antialiased">
        <ErrorView
          title={
            <>
              앱을 불러오지
              <br />
              못했어요
            </>
          }
          description="지금까지 저장한 기록은 그대로 있어요. 다시 불러오면 대부분 해결돼요."
          digest={error.digest}
          action={
            <>
              <Button block onClick={reset}>
                다시 시도
              </Button>
              {/* eslint-disable-next-line @next/next/no-location-assign-relative-destination --
                  라우터로 옮기라는 규칙인데, 여기는 **루트 레이아웃이 터진 화면**이라
                  그 라우터가 살아 있다는 보장이 없다. 통짜 새로고침이 이 화면의 요점이다. */}
              <Button variant="secondary" block onClick={() => window.location.assign("/")}>
                처음 화면으로
              </Button>
            </>
          }
        />
      </body>
    </html>
  );
}
