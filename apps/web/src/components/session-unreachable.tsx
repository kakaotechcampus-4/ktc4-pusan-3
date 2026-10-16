"use client";

import { ErrorView } from "@/components/error-view";
import { Button } from "@/components/ui/button";
import { useSessionStore } from "@/stores/session";

/**
 * 앱을 켰는데 로그인 상태를 확인하지 못했다 (서버에 닿지 못함).
 *
 * 🚨 **로그인 화면으로 보내지 않는다.** refresh 쿠키는 그대로라 연결이 돌아오면 그대로 이어진다.
 *    여기서 로그인 화면을 세우면 보호자는 로그아웃된 줄 알고 카카오 로그인을 다시 하고, 그것도
 *    연결이 없어 실패한다 (docs/web/auth-consent-v1.md §2).
 */
export function SessionUnreachable() {
  const restore = useSessionStore((s) => s.restore);

  return (
    <ErrorView
      title={
        <>
          연결을 확인해
          <br />
          주세요
        </>
      }
      description="로그인 상태를 확인하지 못했어요. 인터넷에 연결된 뒤 다시 시도하면 이어서 쓸 수 있어요."
      action={
        <Button block onClick={() => void restore()}>
          다시 시도
        </Button>
      }
    />
  );
}
