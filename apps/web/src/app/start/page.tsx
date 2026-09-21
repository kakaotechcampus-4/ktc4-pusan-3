"use client";

import { useQuery } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useEffect } from "react";

import { AuthGate } from "@/components/auth-gate";
import { Button } from "@/components/ui/button";
import { Card, CardFailed } from "@/components/ui/card";
import { PageTitle } from "@/components/ui/page-title";
import { Screen } from "@/components/ui/screen";
import { Spinner } from "@/components/ui/spinner";
import { api, qk, type Me } from "@/lib/api";

/**
 * 00-1 경로 고르기 — **아이가 0명인 계정만 지나는 화면이다.**
 *
 * 왜 이 화면이 있나 (#96) — 엄마가 아이를 등록한 뒤 아빠가 초대를 기다리지 않고 그냥
 * 가입해서 **같은 아이를 또 등록**하는 일이 생긴다. 아이는 보호자당 한 명이라, 그러면
 * 아빠는 나중에 초대를 수락할 수 없다 (`409 child_already_exists`). 되돌리는 길이 없어서
 * **고르기 전에 묻는다.**
 *
 * 🚨 **신규 가입자 전용이 아니다.** 기존 회원이 아직 아이가 없을 수도 있고(초대를 기다리는
 *    중), 그 사람에게도 코드 입력 경로가 필요하다. 그래서 기준은 "신규" 가 아니라
 *    **아이 0명**이다 — 콜백 화면도 같은 기준으로 여기 보낸다.
 *
 * 🚨 아이가 이미 있으면 이 화면이 할 말이 없다. 홈으로 되돌린다.
 */
export default function StartPage() {
  return (
    <AuthGate>
      <StartScreen />
    </AuthGate>
  );
}

function StartScreen() {
  const router = useRouter();

  const me = useQuery({ queryKey: qk.me(), queryFn: () => api.get<Me>("/me") });

  const firstChildId = me.data?.children[0]?.child_id ?? null;
  useEffect(() => {
    if (firstChildId) router.replace(`/child/${firstChildId}/home`);
  }, [firstChildId, router]);

  // 아이가 있으면 위 effect 가 옮긴다. 그 한 프레임에 선택지를 그리지 않는다.
  if (me.isPending || firstChildId) {
    return (
      <Screen className="justify-center">
        <p className="text-body text-ink-muted flex items-center justify-center gap-2">
          <Spinner />
          불러오는 중…
        </p>
      </Screen>
    );
  }

  return (
    <Screen className="gap-6">
      <div>
        <PageTitle>
          어떻게
          <br />
          시작할까요
        </PageTitle>
        <p className="text-body text-ink-muted mt-3">
          아이를 새로 등록하거나, 이미 등록한 보호자에게 받은 코드로 참여할 수 있어요.
        </p>
      </div>

      {me.isError ? (
        <CardFailed>
          <p>내 정보를 불러오지 못했어요. 두 가지 중에 골라도 되고, 다시 시도해도 돼요.</p>
        </CardFailed>
      ) : null}

      {/*
        🚨 이 문장이 이 화면의 이유다. 버튼 위에 둔다 — 고른 뒤에 읽으면 늦다.
        🚨 `Banner` 를 쓰지 않는다. `caution` 은 승인 게이트 2곳, `danger` 는 알레르기 전용이라
           (디자인 시스템 §3) 여기에 쓰면 그 두 색의 뜻이 옅어진다. 잘못 고르면 되돌릴 수
           없는 것은 맞지만, 이건 **되돌릴 수 없는 실행을 승인하는 자리가 아니다.**
      */}
      <Card>
        <p className="text-body-sm text-ink-muted">
          한 보호자는 아이를 한 명만 등록할 수 있어요.{" "}
          <strong className="text-ink">초대를 받았다면 새로 등록하지 말고</strong> 아래에서 코드를
          입력해 주세요.
        </p>
      </Card>

      <div className="mt-auto flex flex-col gap-3 pt-2">
        <div className="flex flex-col gap-1.5">
          <Button block onClick={() => router.push("/onboarding")}>
            아이를 등록할게요
          </Button>
          <p className="text-caption text-ink-subtle text-center">
            처음 등록하는 보호자예요. 별명과 생일부터 받아요.
          </p>
        </div>

        <div className="flex flex-col gap-1.5">
          <Button block variant="secondary" onClick={() => router.push("/invite")}>
            초대 코드를 받았어요
          </Button>
          <p className="text-caption text-ink-subtle text-center">
            이미 등록된 아이에 함께 연결돼요. 아이 정보를 다시 입력하지 않아요.
          </p>
        </div>
      </div>
    </Screen>
  );
}
