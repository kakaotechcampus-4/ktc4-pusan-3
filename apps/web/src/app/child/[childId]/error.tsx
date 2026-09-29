"use client";

import { useParams } from "next/navigation";
import { useEffect } from "react";

import { ErrorView } from "@/components/error-view";
import { Button, ButtonLink } from "@/components/ui/button";
import { reportRenderError } from "@/lib/report-render-error";

/**
 * 03~09 아이 화면의 바운더리. `app/error.tsx` 와 같은 일을 하되 **더 가까이서** 받는다 —
 * 이 파일이 `child/[childId]/layout.tsx` 안쪽이라 `ChildScope` 가 살아남고,
 * 되돌아갈 곳도 `/` 가 아니라 **보고 있던 아이의 홈**이 된다.
 *
 * 🚨 `childId` 는 URL 에서 읽는다 (`ChildScope` 주석 — 정본은 URL 이고 스토어가 아니다).
 *    훅 순서를 갈라야 하므로 값이 없을 때도 훅은 그대로 부르고 링크만 `/` 로 떨어뜨린다.
 */
export default function ChildError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  const params = useParams<{ childId: string }>();
  const childId = params?.childId;

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
      description="지금까지 저장한 기록은 그대로 있어요. 다시 시도해 보시고, 계속 같으면 잠시 뒤에 다시 열어 주세요."
      digest={error.digest}
      action={
        <>
          <Button block onClick={reset}>
            다시 시도
          </Button>
          <ButtonLink href={childId ? `/child/${childId}/home` : "/"} variant="secondary" block>
            홈으로
          </ButtonLink>
        </>
      }
    />
  );
}
