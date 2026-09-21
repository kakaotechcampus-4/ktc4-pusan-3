"use client";

import { useQuery } from "@tanstack/react-query";
import { Baby, MailPlus } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect } from "react";

import { AuthGate } from "@/components/auth-gate";
import { SettingsGroup, SettingsLinkRow } from "@/components/settings-row";
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
 * 🚨 **버튼 두 개를 바닥에 세우지 않는다.** 두 갈래가 이 화면의 **본문**이라 위에 선다.
 *    바닥에 붙이면 ① 내용이 셋뿐이라 가운데가 한 폭 비고 (05 화면과 같은 이유),
 *    ② `primary` + `secondary` 가 되어 **화면이 "등록" 쪽을 권하는 셈**이 된다 — 이 화면이
 *    막으려는 실수가 바로 그 잘못된 등록이다. 쉐브론 줄 둘은 서열 없이 나란히 선다.
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

      {/* 🚨 10 설정과 같은 줄을 쓴다 — **누르면 다른 화면으로 가는 줄**이라는 점이 같다.
          여기서 라디오나 칩을 쓰면 확인 버튼이 한 번 더 필요해진다 (08 사진 시트의 1단계와
          같은 판단: 고르면 곧바로 넘어가는 자리는 라디오가 아니라 길이다). */}
      <SettingsGroup>
        <SettingsLinkRow
          href="/onboarding"
          icon={Baby}
          title="아이를 등록할게요"
          status="처음 등록하는 보호자예요"
          note="별명과 생일부터 받아요. 아이 정보에 대한 동의도 그 화면에서 함께 받아요."
        />
        <SettingsLinkRow
          href="/invite"
          icon={MailPlus}
          title="초대 코드를 받았어요"
          status="이미 등록된 아이에 함께 연결돼요"
          note="아이 정보를 다시 입력하지 않아요. 코드는 아이를 등록한 보호자가 만들어요."
        />
      </SettingsGroup>

      {/* 🚨 고른 **뒤**에 읽으면 늦다. 줄 바로 아래에 둔다.
          🚨 `Banner` 를 쓰지 않는다. `caution` 은 승인 게이트 2곳, `danger` 는 알레르기
             전용이라 (디자인 시스템 §3) 여기에 쓰면 그 두 색의 뜻이 옅어진다. */}
      <Card>
        <p className="text-body-sm text-ink-muted">
          한 보호자는 아이를 한 명만 등록할 수 있어요.{" "}
          <strong className="text-ink">초대를 받았다면 새로 등록하지 말고</strong> 코드를 입력해
          주세요. 먼저 등록해 버리면 초대를 받을 수 없어요.
        </p>
      </Card>

      {me.isError ? (
        <CardFailed>내 정보를 불러오지 못했어요. 둘 중에 골라도 되고, 다시 열어도 돼요.</CardFailed>
      ) : null}
    </Screen>
  );
}
