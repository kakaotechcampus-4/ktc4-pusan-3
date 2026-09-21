"use client";

import { ArrowLeft, LifeBuoy, Plus } from "lucide-react";
import { useState, type ReactNode } from "react";

import { AccountSection } from "@/components/account-section";
import { AuthGate } from "@/components/auth-gate";
import { ChildNav } from "@/components/child-nav";
import { ConsentSection, LegalDocumentSection } from "@/components/consent-section";
import { ParentSection } from "@/components/parent-section";
import { SettingsGroup, SettingsLinkRow } from "@/components/settings-row";
import { ICON_SIZE, ICON_STROKE } from "@/components/ui/icon";
import { IconButton, IconButtonLink } from "@/components/ui/icon-button";
import { PageTitle } from "@/components/ui/page-title";
import { Screen } from "@/components/ui/screen";
import { useChildId } from "@/hooks/use-child-id";

/**
 * 10 설정 — **지금 이렇게 되어 있어요.**
 *
 * 이 화면은 고치러 오는 곳이 아니라 **되돌려 받으러** 오는 곳이다. 이 앱이 아이에 대해 아는
 * 것은 전부 보호자가 준 것이고, 여기서 하는 한 가지 일은 "지금 무엇을 줬고 무엇을 거둬들일
 * 수 있는가" 를 숨김없이 내놓는 것이다. 그래서 **줄마다 지금 상태를 글자로 달고** 선다.
 *
 * 🚨 **맨 위에 현황 요약을 두지 않는다.** 한동안 `card-accent` 한 장에 모아 뒀는데, 그 값들이
 *    바로 아래 구역들이 이미 말하는 것이라 **같은 사실을 두 번** 적는 꼴이었다.
 *
 * 🚨 **위에서 아래로 "손대는 것 → 읽는 것 → 나가는 것" 이다.** 지금 켜고 끌 수 있는 동의가
 *    위에 서고, 읽기만 하는 약관 목록이 그 아래, 계정(로그아웃·탈퇴)이 마지막이다.
 *    손댈 수 없는 넷이 손댈 수 있는 하나보다 위에서 자리를 먹지 않게 한다.
 *
 * 🚨 **아이 정보를 여기서 다루지 않는다.** 이름·생일·알레르기는 11 아이 프로필이 소유하고,
 *    거기로 가는 길도 하단 네비가 든다 — 설정에 가는 길을 하나 더 두면 같은 화면으로 가는
 *    문이 둘이 된다 (제품 결정 · #89).
 *
 * 🚨 **네비에서는 홈이 켜진 채**다. 설정은 홈 헤더에서 들어가는 곁가지라 자기 칸이 없고,
 *    아무 칸도 안 켜진 네비는 고장난 것처럼 보인다 (`ChildNav` 주석).
 *    🚨 그건 시각적 결정이라 `onRoute={false}` 로 `aria-current` 는 뺀다 — 제목이 "설정" 인데
 *    스크린리더가 "홈, 현재 페이지" 라고 읽으면 그건 사실이 아니다.
 *
 * 🚨 **아이 이름·생일과 알레르기는 여기 있지 않다.** 11 아이 프로필 화면이 가져갔다 (#74) —
 *    아이 자체에 대한 것은 **네비의 "아이" 칸이 소유하고**, 여기는 계정과 공유만 다룬다.
 *    한 기능이 두 화면에 서면 어느 쪽이 정본인지 부모가 판단해야 한다. 그래서 설정에는
 *    프로필로 **가는 길도 두지 않는다** — 네비가 이미 들고 있어서 문이 둘이 된다.
 *
 * 🚨 **이 화면에 `primary` 버튼이 없다.** 설정에는 "다음에 할 행동" 이 없다 — 하나를 세우면
 *    화면이 그걸 권하는 셈이 되는데, 여기서 권할 것은 아무것도 없다 (디자인 시스템 §7).
 *
 * 🚨 **승인 게이트가 아니다.** `btn-approve` 와 `caution` 은 캘린더 쓰기·건강 기록 확정
 *    두 곳 전용이다 (최상위 CLAUDE.md §2). 파괴적 확정에만 `danger` 를 쓴다.
 */
export default function SettingsPage() {
  return (
    <AuthGate>
      <SettingsScreen />
    </AuthGate>
  );
}

function SettingsScreen() {
  const childId = useChildId();
  const [inviteOpen, setInviteOpen] = useState(false);

  return (
    <Screen className="gap-6" nav={<ChildNav active="home" onRoute={false} />}>
      <header>
        {/* 🚨 **화살표는 링크다** (`IconButtonLink`). `router.back()` 은 어디서 왔는지에
            따라 달라져서, 알림이나 링크로 바로 들어오면 돌아갈 데가 없다.
            🚨 **줄 전체를 `-ml-3` 로 민다.** 44px 원 안에 20px 아이콘이 가운데 있어 좌우
            12px 이 비는데, 그대로 두면 화살표가 화면 왼쪽 기준선보다 안쪽에 선다.
            ⚠️ 제목은 화살표 폭만큼 안으로 들어간다 — 아래 구역 머리줄들과 **왼쪽이 안 맞는
            유일한 요소**다. 가로로 붙이는 한 피할 수 없다(44px 타깃이 16px 여백보다 넓다). */}
        <div className="-ml-3 flex items-center gap-1">
          <IconButtonLink href={`/child/${childId}/home`} label="홈으로 돌아가기">
            <ArrowLeft aria-hidden size={ICON_SIZE.md} strokeWidth={ICON_STROKE} />
          </IconButtonLink>
          <PageTitle>설정</PageTitle>
        </div>
        <p className="text-body-sm text-ink-muted mt-2">함께 보는 사람과 동의를 여기서 관리해요.</p>
      </header>

      <Section
        title="함께 보는 보호자"
        description="같은 아이를 함께 보는 사람이에요. 초대는 링크 하나로 해요."
        action={
          <IconButton label="보호자 초대하기" onClick={() => setInviteOpen(true)} className="-my-1">
            <Plus aria-hidden size={ICON_SIZE.md} strokeWidth={ICON_STROKE} />
          </IconButton>
        }
      >
        <ParentSection
          childId={childId}
          inviteOpen={inviteOpen}
          onInviteOpenChange={setInviteOpen}
        />
      </Section>

      <Section
        title="동의"
        description="지금 켜고 끌 수 있는 것이에요. 바꾸기 전에 무엇이 달라지는지 먼저 보여드리고, 이름을 누르면 전문을 볼 수 있어요."
      >
        <ConsentSection childId={childId} />
      </Section>

      <Section title="고객센터" description="자주 묻는 것과 이 서비스의 기본 정보예요.">
        <SettingsGroup>
          <SettingsLinkRow
            href={`/child/${childId}/settings/help`}
            icon={LifeBuoy}
            title="자주 묻는 질문"
            status="기록 · 제안 · 안전 · 함께 보기"
          />
        </SettingsGroup>
      </Section>

      {/* 🚨 **약관은 계정 바로 위다.** 읽기만 하는 문서 목록이라 손대는 구역들보다 뒤에 오되,
          계정(로그아웃·탈퇴)보다는 앞에 둔다 — 나가는 길이 화면의 마지막이어야 한다. */}
      {/* 🚨 **동의 스코프가 아니라 읽는 문서 둘이다.** 필수 동의 네 건은 가입 화면이 받는
          것이라 설정에 다시 세우지 않는다 — 여기서 부모가 하려는 일은 동의 이력 확인이
          아니라 약관을 읽는 것이다 (`LegalDocumentSection`). */}
      <Section title="약관과 방침" description="누르면 전문을 볼 수 있어요.">
        <LegalDocumentSection />
      </Section>

      {/* 🚨 **탈퇴가 계정 구역 안에 있다.** 로그인·로그아웃과 같은 축이라 따로 떼어 두면
          부모가 설정 전체를 훑어야 찾는다. 실행은 여기서 안 하고 다음 화면이 진다. */}
      <Section title="계정" description="로그인에 쓰는 정보와 나가는 길이에요.">
        <AccountSection childId={childId} />
      </Section>
    </Screen>
  );
}

/**
 * 구역 머리줄. 🚨 **더하는 행동은 머리줄로 올린다** — 목록 아래 전체 폭 버튼으로 두면
 * `secondary` 의 실루엣이 목록 상자와 같아서 둥근 사각형이 연달아 서고, 구역이 넷이라
 * 그것만으로 화면이 상자의 나열이 된다.
 */
function Section({
  title,
  description,
  action,
  children,
}: {
  title: string;
  description: string;
  action?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="flex flex-col gap-3">
      <div>
        {/* 🚨 `items-center` 다. 버튼이 있는 줄에서 `items-baseline` 을 쓰면 버튼 상자가
            글자 기준선에 매달려 라벨보다 아래로 처진다. */}
        <div className="flex items-center justify-between gap-2">
          <h2 className="text-label text-brand">{title}</h2>
          {action}
        </div>
        <p className="text-body-sm text-ink-muted mt-1">{description}</p>
      </div>
      {children}
    </section>
  );
}
