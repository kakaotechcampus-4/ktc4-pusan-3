"use client";

import { ArrowLeft, ChevronDown, ChevronUp } from "lucide-react";

import { AuthGate } from "@/components/auth-gate";
import { ChildNav } from "@/components/child-nav";
import { Card } from "@/components/ui/card";
import { ICON_SIZE, ICON_STROKE } from "@/components/ui/icon";
import { IconButtonLink } from "@/components/ui/icon-button";
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
 * **접었다 편다. 기본은 닫힘이다** (제품 결정). 질문 열두 개의 답이 다 펼쳐져 있으면 스크롤이
 * 길어서, 찾는 사람이 자기 질문을 지나쳐 버린다 — 닫아 두면 **질문 목록**이 먼저 보인다.
 *
 * 🚨 **네이티브 `<details>`/`<summary>` 위에 얹는다.** 키보드 · 포커스 · 스크린리더의
 *    펼침 상태(`expanded`)를 브라우저가 준다. 이 저장소가 시트를 `<dialog>` 로, 달력을
 *    `react-day-picker` 로 고른 것과 **같은 기준**이다 — 손으로 짜면 반드시 빠뜨리는 것만
 *    남의 것을 쓴다 (`apps/web/CLAUDE.md` §3). `aria-expanded` 를 손으로 달지 않는다.
 *
 * 🚨 **한 번에 하나만 열리게 하지 않는다** (`name` 속성을 주지 않는다). 두 답을 나란히 놓고
 *    비교하는 것을 막을 이유가 없고, 방금 연 것이 다른 것을 닫으면 화면이 제멋대로 움직인다.
 *
 * 🚨 **여는 애니메이션이 없고 쉐브론도 회전하지 않는다** (디자인 시스템 §8). 방향은 아이콘을
 *    갈아 끼워 말한다 — 고르기 상자가 쓰는 것과 같은 처리다.
 *
 * 🚨 **문의 버튼을 만들지 않는다.** 이메일도 카카오 채널도 아직 없다. 없는 주소를 버튼으로
 *    세우면 부모가 보낸 문의가 아무 데도 도착하지 않는다 — 아무 데도 안 가는 링크를 만들지
 *    않는다는 규칙과 같은 자리다 (`lib/help.ts`). 채널이 정해지면 그때 붙인다.
 *
 * 🚨 **사업자 정보를 지어내지 않는다.** 이 서비스에는 아직 없다 — 확인할 수 있는 사실만 둔다.
 *
 * 🚨 **네비를 붙인다.** 흐름 중인 화면이 아니라 읽는 화면이라, 읽다가 다른 데로 가도
 *    끝나지 않는 절차가 없다 (탈퇴 화면은 반대다 · `apps/web/CLAUDE.md` §3).
 *
 * 🚨 **돌아가기는 위 화살표 하나다.** 아래에 같은 이름의 링크를 하나 더 뒀었는데, 스크린리더의
 *    링크 목록에 **같은 이름이 두 번** 뜨고 둘 다 같은 데로 갔다. 아래로 다 읽고 나가는 길은
 *    하단 네비가 이미 들고 있다 (탈퇴 화면은 다르다 — 거기 아래 버튼은 "그만둔다" 는 뜻이라
 *    이름도 하는 일도 화살표와 다르다).
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
        {/* 🚨 **화살표는 링크다** (`IconButtonLink`). `router.back()` 은 어디서 왔는지에
            따라 달라져서, 알림이나 링크로 바로 들어오면 돌아갈 데가 없다.
            🚨 **줄 전체를 `-ml-3` 로 민다.** 44px 원 안에 20px 아이콘이 가운데 있어 좌우
            12px 이 비는데, 그대로 두면 화살표가 화면 왼쪽 기준선보다 안쪽에 선다.
            ⚠️ 제목은 화살표 폭만큼 안으로 들어간다 — 아래 구역 머리줄들과 **왼쪽이 안 맞는
            유일한 요소**다. 가로로 붙이는 한 피할 수 없다(44px 타깃이 16px 여백보다 넓다). */}
        <div className="-ml-3 flex items-center gap-1">
          <IconButtonLink href={`/child/${childId}/settings`} label="설정으로 돌아가기">
            <ArrowLeft aria-hidden size={ICON_SIZE.md} strokeWidth={ICON_STROKE} />
          </IconButtonLink>
          <PageTitle>고객센터</PageTitle>
        </div>
        <p className="text-body-sm text-ink-muted mt-2">
          자주 묻는 것들을 모아 뒀어요. 질문을 누르면 답이 펼쳐져요.
        </p>
      </header>

      {FAQ_GROUPS.map((group) => (
        <section key={group.heading} className="flex flex-col gap-3">
          <h2 className="text-label text-brand">{group.heading}</h2>
          <div className="flex flex-col gap-3">
            {group.items.map((item) => (
              <Card key={item.question}>
                <details className="group">
                  {/* 🚨 **기본 삼각형 마커를 지운다** (`list-none` + `::-webkit-details-marker`).
                      뜻을 나르는 그림은 lucide 한 곳에 모으기로 했고(문서 §4), 브라우저마다
                      다르게 생긴 마커가 그 규칙 밖에서 하나 더 서면 안 된다.
                      🚨 **여백을 카드 밖으로 되밀어 줄 전체를 누를 수 있게 한다**
                      (`-m-4 p-4`) — 글자만 누르게 하면 타깃이 44px 을 못 채운다.
                      🚨 **네 방향 다 되민다.** 아래만 남겨 뒀더니 카드의 `p-4` 와 summary 의
                      `pb-4` 가 겹쳐 **닫힌 카드 아래에 32px 빈 띠**가 생겼다. 펼친 답과의
                      간격은 답을 감싼 `div` 의 `mt-4` 가 진다.
                      🚨 질문이 제목이다. 물음표를 아이콘으로 바꾸지 않는다 — 스크린리더가
                      훑을 때 남는 것이 이 줄이고, 아이콘은 목록에서 아무 말도 안 한다. */}
                  <summary className="text-section text-ink -m-4 flex cursor-pointer list-none items-center justify-between gap-3 p-4 focus-visible:-outline-offset-2 [&::-webkit-details-marker]:hidden">
                    <h3 className="text-section text-ink min-w-0">{item.question}</h3>
                    <ChevronDown
                      aria-hidden
                      size={ICON_SIZE.md}
                      strokeWidth={ICON_STROKE}
                      className="text-ink-subtle shrink-0 group-open:hidden"
                    />
                    <ChevronUp
                      aria-hidden
                      size={ICON_SIZE.md}
                      strokeWidth={ICON_STROKE}
                      className="text-ink-subtle hidden shrink-0 group-open:block"
                    />
                  </summary>
                  <div className="mt-4">
                    {item.answer.map((paragraph, index) => (
                      <p
                        key={paragraph}
                        className={
                          index === 0
                            ? "text-body-sm text-ink-muted"
                            : "text-body-sm text-ink-muted mt-2"
                        }
                      >
                        {paragraph}
                      </p>
                    ))}
                  </div>
                </details>
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
    </Screen>
  );
}
