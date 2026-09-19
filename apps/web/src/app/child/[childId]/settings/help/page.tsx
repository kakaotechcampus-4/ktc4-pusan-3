"use client";

import Link from "next/link";

import { AuthGate } from "@/components/auth-gate";
import { ChildNav } from "@/components/child-nav";
import { Card } from "@/components/ui/card";
import { PageTitle } from "@/components/ui/page-title";
import { Screen } from "@/components/ui/screen";
import { useChildId } from "@/hooks/use-child-id";
import { CONTACT_NOT_READY, FAQ_GROUPS, SERVICE_INFO } from "@/lib/help";

/**
 * 10 설정 › 고객센터 — **읽는 화면이다.**
 *
 * 부모가 여기 오는 이유는 무언가를 고치려는 게 아니라 **"이게 맞나?" 를 확인하려는** 것이다.
 * 그래서 이 화면에는 누를 것이 거의 없고, 구조가 읽는 순서를 만든다 — 질문이 `section` 으로
 * 서고 답이 그 아래 문단으로 붙는다.
 *
 * 🚨 **접었다 펴지 않는다.** 아코디언을 쓰면 찾는 사람이 질문을 하나씩 열어 봐야 하고,
 *    한 번에 훑어 답이 여기 없다는 것을 아는 길이 사라진다. 제목만 읽고 건너뛰는 것이
 *    여는 것보다 빠르다 — 게다가 이 시스템에는 접는 컴포넌트가 없고, 이 화면 하나 때문에
 *    새 상호작용을 만들지 않는다 (디자인 시스템 §7).
 *
 * 🚨 **문의 버튼을 만들지 않는다.** 이메일도 카카오 채널도 아직 없다. 없는 주소를 버튼으로
 *    세우면 부모가 보낸 문의가 아무 데도 도착하지 않는다 — 아무 데도 안 가는 링크를 만들지
 *    않는다는 규칙과 같은 자리다 (`lib/help.ts`). 채널이 정해지면 그때 붙인다.
 *
 * 🚨 **사업자 정보를 지어내지 않는다.** 이 서비스에는 아직 없다 — 확인할 수 있는 사실만 둔다.
 *
 * 🚨 **네비를 붙인다.** 흐름 중인 화면이 아니라 읽는 화면이라, 읽다가 다른 데로 가도
 *    끝나지 않는 절차가 없다 (탈퇴 화면은 반대다 · `apps/web/CLAUDE.md` §3).
 */
export default function HelpPage() {
  return (
    <AuthGate>
      <HelpScreen />
    </AuthGate>
  );
}

function HelpScreen() {
  const childId = useChildId();

  return (
    <Screen className="gap-8" nav={<ChildNav active="home" onRoute={false} />}>
      <header>
        <PageTitle>고객센터</PageTitle>
        <p className="text-body-sm text-ink-muted mt-2">
          자주 묻는 것들을 모아 뒀어요. 이 서비스가 하는 일과 하지 않는 일이 함께 적혀 있어요.
        </p>
      </header>

      {FAQ_GROUPS.map((group) => (
        <section key={group.heading} className="flex flex-col gap-3">
          <h2 className="text-label text-brand">{group.heading}</h2>
          <div className="flex flex-col gap-3">
            {group.items.map((item) => (
              <Card key={item.question}>
                {/* 🚨 질문이 제목이다. 물음표를 아이콘으로 바꾸지 않는다 — 스크린리더가
                    훑을 때 남는 것이 이 줄이고, 아이콘은 목록에서 아무 말도 안 한다. */}
                <h3 className="text-section text-ink">{item.question}</h3>
                {item.answer.map((paragraph) => (
                  <p key={paragraph} className="text-body-sm text-ink-muted mt-2">
                    {paragraph}
                  </p>
                ))}
              </Card>
            ))}
          </div>
        </section>
      ))}

      <section className="flex flex-col gap-3">
        <h2 className="text-label text-brand">서비스 정보</h2>
        <Card>
          <dl className="flex flex-col gap-2">
            {SERVICE_INFO.map((row) => (
              <div key={row.label} className="flex items-baseline justify-between gap-3">
                <dt className="text-body-sm text-ink-muted">{row.label}</dt>
                <dd className="text-body-sm text-ink">{row.value}</dd>
              </div>
            ))}
          </dl>
          <p className="border-line text-caption text-ink-subtle mt-3 border-t pt-3">
            약관과 동의 전문은 설정의 동의 구역에서 볼 수 있어요.
          </p>
        </Card>
        <p className="text-caption text-ink-subtle">{CONTACT_NOT_READY}</p>
      </section>

      <Link
        href={`/child/${childId}/settings`}
        className="text-button text-ink-muted border-line rounded-field min-h-touch bg-surface ease-standard hover:bg-surface-muted active:bg-surface-muted mt-auto inline-flex items-center justify-center px-4 transition-colors duration-120 focus-visible:-outline-offset-2"
      >
        설정으로 돌아가기
      </Link>
    </Screen>
  );
}
