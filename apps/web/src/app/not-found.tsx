import type { Metadata } from "next";

import { ErrorView } from "@/components/error-view";
import { ButtonLink } from "@/components/ui/button";

export const metadata: Metadata = {
  title: "없는 주소",
};

/**
 * 404. 어디에도 걸리지 않은 경로와 `notFound()` 호출이 여기로 온다.
 *
 * 🚨 **아이 화면으로 되돌리지 않는다.** 여기는 아이 스코프 밖이라 `childId` 를 모르고,
 *    URL 에서 주워 만든 주소로 보내면 남의 아이 화면을 두드리게 된다. 길은 `/` 하나다
 *    (로그인 여부에 따라 그 화면이 알아서 갈린다).
 *
 * 서버 컴포넌트다. 상태도 훅도 없으니 `"use client"` 를 붙이지 않는다.
 */
export default function NotFound() {
  return (
    <ErrorView
      title={
        <>
          없는
          <br />
          주소예요
        </>
      }
      description="주소가 바뀌었거나 링크가 잘못된 것 같아요. 처음 화면에서 다시 들어와 주세요."
      action={
        <ButtonLink href="/" block>
          처음 화면으로
        </ButtonLink>
      }
    />
  );
}
