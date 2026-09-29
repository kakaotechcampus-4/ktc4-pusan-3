"use client";

import { ErrorBoundary } from "react-error-boundary";
import type { ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { CardFailed } from "@/components/ui/card";
import { reportRenderError } from "@/lib/report-render-error";

/**
 * **서브트리 하나만** 격리하는 바운더리. 라우트 단위(`app/error.tsx`)가 화면을 통째로
 * 바꾸는 것과 달리, 여기 감싼 부분만 `CardFailed` 로 바뀌고 나머지 화면은 그대로 산다.
 *
 * 왜 필요한가 — 서버·LLM 이 만든 모양을 그대로 그리는 자리가 있다 (제안 카드 · 성장 그래프 ·
 * 실행 결과). 거기서 필드 하나가 예상과 다르면 지금은 **화면 전체가** 에러 화면으로 간다.
 * 성공한 나머지까지 같이 사라지는 것이라 NF-06 이 말하는 "성공과 실패를 한 화면에" 와
 * 어긋난다.
 *
 * ## 붙일 곳
 * - 서드파티 렌더러를 통과하는 곳 — 11-1 성장 그래프(Recharts), 09 달력(react-day-picker)
 * - 서버·LLM 이 만든 목록의 **항목 하나** — 제안 카드, 실행 결과 카드
 *
 * ## 🚨 붙이지 않을 곳
 * - **승인 게이트 2곳** (캘린더 쓰기 · 건강·알레르기 확정). 승인 시트가 터졌는데 작은 회색
 *   카드로 조용히 바뀌면 보호자가 "확정됐다 / 안 됐다" 를 구분할 수 없다. 되돌릴 수 없는
 *   것 앞에서는 화면째 실패하는 편이 안전하다 (최상위 CLAUDE.md §2 실행).
 * - **API 실패**. 바운더리는 렌더 중 예외만 잡는다 — `useQuery` 의 실패는 여기 안 온다.
 *   그건 화면이 `isError` → `CardFailed` 로 그 자리에 그린다. `throwOnError` 로 올리지 말 것.
 *
 * `resetKeys` 는 값이 바뀌면 자동으로 다시 그려 본다 — 아이가 바뀌거나 탭이 바뀌면
 * 이전 화면의 실패를 들고 있을 이유가 없다.
 *
 * 🚨 **"다시 시도" 가 같은 것을 또 그리면 실패도 그대로다.** 터진 원인이 들고 있던 값이라면
 *    `onReset` 에서 그 값을 비워야 한다 (쿼리를 다시 받거나, 펼쳐 둔 항목을 접거나).
 *    비울 것이 없을 때만 `onReset` 을 생략한다.
 */
export function AppErrorBoundary({
  label,
  resetKeys,
  onReset,
  children,
}: {
  /** 이 자리가 무엇이었는지 한 줄로. 🚨 에러 메시지를 넣지 않는다 (원문이 실릴 수 있다). */
  label: string;
  resetKeys?: unknown[];
  /** "다시 시도" 를 누르면 다시 그리기 **전에** 부른다. 터진 원인이 된 상태를 여기서 비운다. */
  onReset?: () => void;
  children: ReactNode;
}) {
  return (
    <ErrorBoundary
      resetKeys={resetKeys}
      onReset={onReset}
      onError={(error) => reportRenderError(error)}
      fallbackRender={({ resetErrorBoundary }) => (
        <CardFailed className="flex flex-col items-start gap-3">
          <p>{label}</p>
          {/* 카드 안의 행동이라 compact 다 (버튼 §크기 주석). 다시 그려 보는 것이 전부고,
              안 되면 같은 카드가 다시 선다 — 안내만 지우는 버튼을 두지 않는다. */}
          <Button variant="tertiary" size="compact" onClick={resetErrorBoundary}>
            다시 시도
          </Button>
        </CardFailed>
      )}
    >
      {children}
    </ErrorBoundary>
  );
}
