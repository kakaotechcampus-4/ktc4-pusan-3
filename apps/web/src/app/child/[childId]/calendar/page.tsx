"use client";

import { AuthGate } from "@/components/auth-gate";
import { ChildNav } from "@/components/child-nav";
import { Card } from "@/components/ui/card";
import { PageTitle } from "@/components/ui/page-title";
import { Screen } from "@/components/ui/screen";

/**
 * 09 캘린더 — **아직 화면이 없다.** 하단 네비가 가리키는 곳이라 라우트를 먼저 뒀다:
 * 아무 데도 안 가는 탭을 만들지 않는다 (00 로그인의 `ready:false` 와 같은 원칙).
 *
 * 다음 이슈에서 `GET/PUT /children/{cid}/calendar/{date}` 와 `PATCH /event-items/{iid}` 를 붙인다.
 */
export default function CalendarPage() {
  return (
    <AuthGate>
      <CalendarScreen />
    </AuthGate>
  );
}

function CalendarScreen() {
  return (
    <Screen className="gap-5" nav={<ChildNav active="calendar" />}>
      <header>
        <PageTitle>캘린더</PageTitle>
        <p className="text-body-sm text-ink-muted mt-2">
          승인한 일정과 준비물, 그날의 일기가 여기 모여요.
        </p>
      </header>

      <Card>
        <p className="text-body text-ink">아직 만드는 중이에요</p>
        <p className="text-body-sm text-ink-muted mt-2">
          승인한 일정이 달에 쌓이고, 준비물은 체크하면 바로 반영돼요. 하루를 누르면 그날 적은 것을
          모아 봅니다.
        </p>
      </Card>
    </Screen>
  );
}
