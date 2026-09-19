"use client";

import { LifeBuoy, Plus, UserRound } from "lucide-react";
import { useState, type ReactNode } from "react";

import { AccountSection } from "@/components/account-section";
import { AuthGate } from "@/components/auth-gate";
import { ChildNav } from "@/components/child-nav";
import { ConsentSection, GrantedConsentSection } from "@/components/consent-section";
import { ParentSection } from "@/components/parent-section";
import { SettingsGroup, SettingsLinkRow } from "@/components/settings-row";
import { ButtonLink } from "@/components/ui/button";
import { ICON_SIZE, ICON_STROKE } from "@/components/ui/icon";
import { IconButton } from "@/components/ui/icon-button";
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
 * 🚨 **위에서 아래로 "내가 쥔 것 → 참고 → 내가 못 쥔 것" 이다.** 지금 켜고 끌 수 있는 것이
 *    위에 서고, 이미 동의해 손댈 수 없는 것은 맨 아래에서 이름과 상태만 간단히 선다.
 *    손댈 수 없는 넷이 손댈 수 있는 하나보다 위에서 자리를 먹지 않게 한다.
 *
 * 🚨 **아이 정보(이름·생일·알레르기)를 여기서 보여주지도 고치지도 않는다.** 11 아이 프로필이
 *    소유한다 — 같은 값을 두 화면에서 고칠 수 있으면 어느 쪽이 정본인지 사라진다.
 *    여기 남는 것은 **가는 길 하나**다.
 *
 * 🚨 **네비에서는 홈이 켜진 채**다. 설정은 홈 헤더에서 들어가는 곁가지라 자기 칸이 없고,
 *    아무 칸도 안 켜진 네비는 고장난 것처럼 보인다 (`ChildNav` 주석).
 *    🚨 그건 시각적 결정이라 `onRoute={false}` 로 `aria-current` 는 뺀다 — 제목이 "설정" 인데
 *    스크린리더가 "홈, 현재 페이지" 라고 읽으면 그건 사실이 아니다.
 *
 * 🚨 **이 화면에 `primary` 버튼이 없다.** 설정에는 "다음에 할 행동" 이 없다 — 하나를 세우면
 *    화면이 그걸 권하는 셈이 되는데, 여기서 권할 것은 아무것도 없다 (디자인 시스템 §7).
 *
 * 🚨 **승인 게이트가 아니다.** `btn-approve` 와 `caution` 은 캘린더 쓰기·건강 기록 확정
 *    두 곳 전용이다 (최상위 CLAUDE.md §2). 동의 철회는 `danger` 를 쓰지만 시트 안 확정
 *    버튼 하나까지다.
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
        <PageTitle>설정</PageTitle>
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

      {/* 🚨 아이 정보를 여기에 **보여주지 않는다.** 이름도 나이도 아바타도 두지 않고
          가는 길만 남긴다 — 같은 값이 두 화면에 뜨면 어느 쪽이 지금 값인지 알 수 없다. */}
      <Section title="아이" description="부르는 이름과 생일, 알레르기는 아이 화면이 들고 있어요.">
        <SettingsGroup>
          <SettingsLinkRow
            href={`/child/${childId}/profile`}
            icon={UserRound}
            title="아이 정보 고치기"
          />
        </SettingsGroup>
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

      <Section title="계정" description="로그인에 쓰는 정보와 나가는 길이에요.">
        <AccountSection />
      </Section>

      {/* 🚨 **이미 동의한 것은 맨 아래다.** 손댈 수 없는 넷이 손댈 수 있는 하나보다 위에서
          자리를 먹지 않게 한다. 그래도 화면에서 빼지는 않는다 — 무엇에 동의했는지 열람할
          경로가 이것 하나뿐이다 (줄의 이름을 누르면 전문 시트). */}
      <Section
        title="이미 동의한 것"
        description="서비스가 성립하는 근거라 화면에서 끌 수 없어요. 이름을 누르면 전문을 볼 수 있어요."
      >
        <GrantedConsentSection childId={childId} />
      </Section>

      {/* 🚨 **탈퇴는 화면 맨 아래에 한 줄로만 선다.** 여기가 이 앱에서 되돌릴 수 없는 것 중
          가장 큰 자리라 안내와 절차를 **다른 화면**이 진다 — 설정 한 줄에서 바로 실행되는
          경로를 만들지 않는다. 🚨 그렇다고 숨기지도 않는다 (접지 않고, 글자를 줄이지 않는다). */}
      <section className="mt-2 flex flex-col gap-2">
        {/* 🚨 **여기에 빨강을 쓰지 않는다.** 이 버튼은 아무것도 확정하지 않고 다음 화면으로
            갈 뿐이다 — `danger` 는 파괴적 **확정**의 색이고, 그 자리는 탈퇴 화면의 확인
            시트다 (디자인 시스템 §7). 가는 길까지 빨강으로 칠하면 색의 뜻이 하나 늘어난다.
            ⚠️ `cn()` 은 tailwind-merge 가 아니라 `className` 으로 글자색을 덮어도
            어느 쪽이 이길지가 생성된 CSS 순서에 달린다 — 덮어쓰지 않는 게 맞다. */}
        <ButtonLink href={`/child/${childId}/settings/withdraw`} variant="tertiary" block>
          탈퇴하기
        </ButtonLink>
        <p className="text-caption text-ink-subtle text-center">
          쌓인 기억이 사라져요. 무엇이 어떻게 되는지 다음 화면에서 알려드려요.
        </p>
      </section>
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
