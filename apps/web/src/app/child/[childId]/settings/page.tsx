"use client";

import { AuthGate } from "@/components/auth-gate";
import { ChildNav } from "@/components/child-nav";
import { Card } from "@/components/ui/card";
import { PageTitle } from "@/components/ui/page-title";
import { Screen } from "@/components/ui/screen";

/**
 * 10 설정 — **아직 화면이 없다.** 03 홈 헤더의 설정 버튼이 가리키는 곳이라 라우트를 먼저 뒀다.
 *
 * 🚨 네비에서는 **홈이 켜진 채**다. 설정은 홈에서 들어가는 곁가지라 자기 칸이 없고,
 *    아무 칸도 안 켜진 네비는 고장난 것처럼 보인다 (`ChildNav` 주석).
 *    🚨 그건 시각적 결정이라 `onRoute={false}` 로 `aria-current` 는 뺀다 — 제목이 "설정" 인데
 *    스크린리더가 "홈, 현재 페이지" 라고 읽으면 그건 사실이 아니다.
 *
 * 🚨 **아이 이름·생일과 알레르기는 여기 있지 않다.** 11 아이 프로필 화면이 가져갔다 (#74) —
 *    아이 자체에 대한 것은 네비의 "아이" 칸이 소유하고, 여기는 **계정과 공유**만 다룬다.
 *    한 기능이 두 화면에 서면 어느 쪽이 정본인지 부모가 판단해야 한다.
 *
 * 다음 이슈에서 `GET /consents` · `/parents` · `/invites` · `DELETE /observations` 를 붙인다.
 * 🚨 파괴적 확정(동의 철회 · 기억 삭제)에만 `btn-danger` 를 쓴다.
 */
export default function SettingsPage() {
  return (
    <AuthGate>
      <SettingsScreen />
    </AuthGate>
  );
}

function SettingsScreen() {
  return (
    <Screen className="gap-5" nav={<ChildNav active="home" onRoute={false} />}>
      <header>
        <PageTitle>설정</PageTitle>
        <p className="text-body-sm text-ink-muted mt-2">
          동의와 함께 보는 보호자를 여기서 관리해요. 아이 정보는 아래 아이 칸에 있어요.
        </p>
      </header>

      <Card>
        <p className="text-body text-ink">아직 만드는 중이에요</p>
        <p className="text-body-sm text-ink-muted mt-2">들어올 것은 이렇습니다.</p>
        <ul className="text-body-sm text-ink-muted mt-3 flex list-disc flex-col gap-1 pl-5">
          <li>함께 보는 보호자 초대와 권한</li>
          <li>동의 관리 · 철회</li>
          <li>쌓인 기록 지우기</li>
        </ul>
      </Card>
    </Screen>
  );
}
