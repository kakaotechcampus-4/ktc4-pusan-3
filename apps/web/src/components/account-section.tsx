"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { DoorOpen, KeyRound } from "lucide-react";
import { useState } from "react";

import { SettingsGroup, SettingsInfoRow } from "@/components/settings-row";
import { BottomSheet } from "@/components/ui/bottom-sheet";
import { Button } from "@/components/ui/button";
import { CardFailed } from "@/components/ui/card";
import { Spinner } from "@/components/ui/spinner";
import { api } from "@/lib/api";
import { useSessionStore } from "@/stores/session";

/**
 * 10 설정 — 계정.
 *
 * 🚨 **계정 탈퇴 버튼을 만들지 않는다.** 엔드포인트가 없고(계약서에도 auth 문서에도),
 *    탈퇴 유예기간이 팀 미정이다 (`docs/api/auth-kakao-v1.md` §미결 1). 없는 기능을 화면에
 *    세워 두고 "준비 중" 을 눌러 알려주면, 정말 지우고 싶어서 온 사람에게 가장 나쁜 자리에서
 *    가장 나쁜 답을 한다. 준비되면 그때 만든다.
 *
 * 🚨 **로그아웃은 파괴가 아니다.** `danger` 를 쓰지 않는다 — 다시 로그인하면 그대로다.
 *    다만 아직 안 보낸 한 줄은 같이 지워지므로(세션 스토어), 그 사실을 확인 시트에 적는다.
 *
 * 🚨 **서버 호출이 실패해도 이 기기에서는 나간다.** 토큰을 들고 남는 것이 더 나쁘다.
 */
export function AccountSection() {
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
