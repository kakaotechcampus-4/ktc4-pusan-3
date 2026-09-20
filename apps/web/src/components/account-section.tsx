"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { DoorOpen, KeyRound, UserMinus } from "lucide-react";
import { useState } from "react";

import { SettingsGroup, SettingsInfoRow, SettingsLinkRow } from "@/components/settings-row";
import { BottomSheet } from "@/components/ui/bottom-sheet";
import { Button } from "@/components/ui/button";
import { CardFailed } from "@/components/ui/card";
import { Spinner } from "@/components/ui/spinner";
import { api } from "@/lib/api";
import { useSessionStore } from "@/stores/session";

/**
 * 10 설정 — 계정.
 *
 * 🚨 **탈퇴가 이 구역에 있다.** 로그인·로그아웃과 같은 축(계정을 어떻게 할 것인가)이라
 *    따로 떼어 두면 부모가 설정 전체를 훑어야 찾는다. 다만 **여기서 실행되지는 않는다** —
 *    줄을 누르면 안내와 절차를 가진 화면으로 간다.
 *
 * 🚨 **탈퇴 줄에 빨강을 쓰지 않는다.** 이 줄은 아무것도 확정하지 않고 다음 화면으로 갈
 *    뿐이다. `danger` 는 파괴적 **확정**의 색이고 그 자리는 탈퇴 화면의 확인 시트다.
 *
 * 🚨 **로그아웃은 파괴가 아니다.** `danger` 를 쓰지 않는다 — 다시 로그인하면 그대로다.
 *    다만 아직 안 보낸 한 줄은 같이 지워지므로(세션 스토어), 그 사실을 확인 시트에 적는다.
 *
 * 🚨 **서버 호출이 실패해도 이 기기에서는 나간다.** 토큰을 들고 남는 것이 더 나쁘다.
 */
export function AccountSection({ childId }: { childId: string }) {
  const router = useRouter();
  const queryClient = useQueryClient();
  const signOut = useSessionStore((s) => s.signOut);
  const [confirming, setConfirming] = useState(false);

  const logout = useMutation({
    mutationFn: () => api.post<null>("/auth/logout"),
    // 🚨 서버가 세션을 못 지워도 이 기기의 토큰은 버린다. 그래서 성공·실패가 같은 경로다.
    onSettled: () => {
      signOut();
      queryClient.clear();
      router.replace("/");
    },
  });

  return (
    <>
      <SettingsGroup>
        <SettingsInfoRow
          icon={KeyRound}
          title="로그인 수단"
          status="카카오"
          note="이메일과 프로필 사진은 받아도 저장하지 않아요"
        />
        <SettingsInfoRow
          icon={DoorOpen}
          title="로그아웃"
          status="이 기기에서만 나가요"
          action={
            <Button variant="tertiary" size="compact" onClick={() => setConfirming(true)}>
              로그아웃
            </Button>
          }
        />
        <SettingsLinkRow
          href={`/child/${childId}/settings/withdraw`}
          icon={UserMinus}
          title="탈퇴하기"
          status="쌓인 기억이 사라져요"
          note="무엇이 어떻게 되는지 다음 화면에서 알려드려요"
        />
      </SettingsGroup>

      <BottomSheet
        open={confirming}
        onClose={() => {
          if (!logout.isPending) setConfirming(false);
        }}
        title="로그아웃할까요?"
        description="쌓인 기억은 그대로 있어요. 다시 로그인하면 이어서 볼 수 있어요."
        footer={
          <div className="flex gap-2">
            <Button
              variant="tertiary"
              className="flex-1"
              disabled={logout.isPending}
              onClick={() => setConfirming(false)}
            >
              그대로 둘게요
            </Button>
            <Button className="flex-1" disabled={logout.isPending} onClick={() => logout.mutate()}>
              {logout.isPending ? <Spinner /> : null}
              로그아웃
            </Button>
          </div>
        }
      >
        <ul className="text-body text-ink marker:text-ink-subtle flex list-disc flex-col gap-2 pl-5">
          <li>아직 보내지 않고 적어 두기만 한 한 줄은 지워져요.</li>
          <li>카카오 계정에서 로그아웃되지는 않아요.</li>
        </ul>
        {logout.isError ? (
          <CardFailed className="mt-4">
            <p>서버에 알리지 못했지만 이 기기에서는 나갔어요.</p>
          </CardFailed>
        ) : null}
      </BottomSheet>
    </>
  );
}
