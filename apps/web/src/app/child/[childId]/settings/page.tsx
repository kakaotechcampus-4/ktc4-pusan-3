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
 * 다음 이슈에서 `GET /consents` · `/parents` · `/invites` · `/health-safety` ·
 * `DELETE /observations` 를 붙인다. 🚨 파괴적 확정(동의 철회 · 기억 삭제)에만 `btn-danger` 를
 * 쓰고, 알레르기 기록 확정은 **승인 게이트 ㉡** 라 지우기 전에 무엇이 없어지는지 보여준다.
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
          아이 정보와 동의, 함께 보는 보호자를 여기서 관리해요.
        </p>
      </header>

      <Card>
        <p className="text-body text-ink">아직 만드는 중이에요</p>
        <p className="text-body-sm text-ink-muted mt-2">들어올 것은 이렇습니다.</p>
        <ul className="text-body-sm text-ink-muted mt-3 flex list-disc flex-col gap-1 pl-5">
          <li>아이 이름과 생일 고치기</li>
          <li>알레르기 · 건강 기록 (보호자가 직접 확인한 값만 저장돼요)</li>
          <li>함께 보는 보호자 초대와 권한</li>
          <li>동의 관리 · 철회</li>
          <li>쌓인 기록 지우기</li>
        </ul>
      </Card>
    </Screen>
  );
}
