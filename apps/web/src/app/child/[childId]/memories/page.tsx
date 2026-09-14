"use client";

import { AuthGate } from "@/components/auth-gate";
import { ChildNav } from "@/components/child-nav";
import { Card } from "@/components/ui/card";
import { PageTitle } from "@/components/ui/page-title";
import { Screen } from "@/components/ui/screen";

/**
 * 07 기억 · 기록 고치기 — **아직 화면이 없다.** 하단 네비가 가리키는 곳이라 라우트를 먼저 뒀다.
 *
 * 다음 이슈에서 `GET /observations` · `GET /affinities` · `POST /corrections` 를 붙인다.
 * 🚨 그때 4버튼 교정(`confirm` / `once_only` / `outdated` / `wrong`)은 전부 `btn-secondary` 다 —
 *    `wrong` 도 빨강이 아니다. 교정은 사고가 아니라 부모가 기억을 고치는 일이다 (문서 §11).
 */
export default function MemoriesPage() {
  return (
    <AuthGate>
      <MemoriesScreen />
    </AuthGate>
  );
}

function MemoriesScreen() {
  return (
    <Screen className="gap-5" nav={<ChildNav active="memories" />}>
      <header>
        <PageTitle>기억</PageTitle>
        <p className="text-body-sm text-ink-muted mt-2">
          쌓인 관찰과 그걸로 만들어진 아이 프로필을 여기서 봅니다.
        </p>
      </header>

      <Card>
        <p className="text-body text-ink">아직 만드는 중이에요</p>
        <p className="text-body-sm text-ink-muted mt-2">
          관찰 하나하나와 거기서 자란 관심을 나눠 보고, 틀린 기억은 네 가지 버튼으로 고칩니다. 받은
          제안이 어땠는지도 여기서 알려줄 수 있어요.
        </p>
      </Card>
    </Screen>
  );
}
