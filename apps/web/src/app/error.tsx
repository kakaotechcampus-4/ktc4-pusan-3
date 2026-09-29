"use client";

import { useEffect } from "react";

import { ErrorView } from "@/components/error-view";
import { Button, ButtonLink } from "@/components/ui/button";
import { reportRenderError } from "@/lib/report-render-error";

/**
 * 라우트 단위 에러 바운더리. **화면을 그리는 도중 던져진 예외**를 받는다.
 *
 * 🚨 **API 실패는 여기로 오지 않고, 오게 만들지도 않는다.** 바운더리는 렌더 중 예외만
 *    잡는다 — `useQuery` 의 실패와 이벤트 핸들러 안의 예외는 도달하지 않는다.
 *    그리고 그게 맞다: 화면마다 `isError` → `CardFailed` 로 **그 자리에** 그려야
 *    "Agent 2개 중 1개만 성공해도 그 화면을 보여준다"(NF-06)가 성립한다.
 *    `throwOnError` 를 켜서 실패를 여기로 올리지 말 것 — 부분 실패가 화면 전체 교체가 된다.
 *
 * 🚨 **루트 레이아웃의 예외는 여기서 못 잡는다.** 이 컴포넌트가 그 레이아웃 **안**에
 *    그려지기 때문이다. 그건 `global-error.tsx` 가 받는다.
 *
 * ⚠️ 개발 서버에서는 Next 의 에러 오버레이가 이 화면을 덮는다. 눈으로 확인할 때는
 *    `pnpm build && pnpm start` 로 본다.
 */
export default function AppError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => reportRenderError(error), [error]);

  return (
    <ErrorView
      title={
        <>
          화면을 여는 중에
          <br />
          문제가 생겼어요
        </>
      }
      // 🚨 "기록은 그대로 있다" 를 먼저 말한다. 이 서비스에서 화면이 비면 부모가 가장 먼저
      //    걱정하는 것은 쌓아 온 기록이고, 그건 서버에 있으므로 사실이다.
      description="지금까지 저장한 기록은 그대로 있어요. 다시 시도해 보시고, 계속 같으면 잠시 뒤에 다시 열어 주세요."
      digest={error.digest}
      action={
        <>
          {/* 다시 그려 보는 것이 이 화면의 첫 번째 행동이다. */}
          <Button block onClick={reset}>
            다시 시도
          </Button>
          <ButtonLink href="/" variant="secondary" block>
            처음 화면으로
          </ButtonLink>
        </>
      }
    />
  );
}
