"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { AuthGate } from "@/components/auth-gate";
import { BottomSheet } from "@/components/ui/bottom-sheet";
import { Button, ButtonLink } from "@/components/ui/button";
import { Card, CardFailed } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { PageTitle } from "@/components/ui/page-title";
import { Screen } from "@/components/ui/screen";
import { Spinner } from "@/components/ui/spinner";
import { useChildId } from "@/hooks/use-child-id";
import { api, type WithdrawRequest, type WithdrawResponse } from "@/lib/api";
import { WITHDRAW_GRACE_DAYS } from "@/lib/consent";
import { useSessionStore } from "@/stores/session";

/**
 * 10 설정 › 탈퇴 — **흐름 중인 화면이다.**
 *
 * 🚨 **하단 네비를 붙이지 않는다.** 고르는 도중에 새는 길을 만들면 그 화면이 끝나지 않는다
 *    (`apps/web/CLAUDE.md` §3). 빠져나가는 길은 자기 "그만두고 돌아가기" 하나다.
 *    고객센터는 읽는 화면이라 반대로 네비를 붙였다.
 *
 * 🚨 **시트 하나로 끝내지 않고 화면을 따로 뒀다.** 이 앱에서 되돌릴 수 없는 것 중 가장 크고,
 *    읽어야 할 것이 시트 한 장에 안 들어간다. 단계는 셋이다 —
 *    ㉠ 무엇이 일어나는지 읽기 ㉡ 읽었다고 표시하기 ㉢ 마지막 확인 시트.
 *
 * 🚨 **승인 게이트가 아니다.** 승인 게이트는 캘린더 쓰기·건강 기록 확정 딱 2곳이고 늘리지
 *    않는다 (최상위 CLAUDE.md §2) — `btn-approve` 도 `caution` 색도 쓰지 않는다.
 *    대신 **파괴적 확정**이라 확정 버튼이 `danger` 다 (디자인 시스템 §7).
 *
 * 🚨 **먼저 덜 무거운 길을 보여준다.** 탈퇴하러 온 사람 중에는 "이것만 멈추고 싶다" 가 섞여
 *    있다. 로그아웃 · 선택 동의 철회 · 보호자 연결 끊기를 위쪽에 둔다.
 *    🚨 그렇다고 탈퇴를 숨기거나 더 어렵게 만들지 않는다 — 되돌릴 권리를 돌려주는 자리에서
 *    그 권리를 미로로 만들면 화면이 스스로를 부정한다. 버튼은 접히지 않은 채 아래에 있다.
 *
 * ⚠️ `POST /auth/withdraw` 는 **계약서에 없고**(`lib/api/types.ts`), 유예기간도 팀 미결이라
 *    `WITHDRAW_GRACE_DAYS` 가 제안값이다 (`lib/consent.ts`). 지금은 MSW 목 위에서만 돈다.
 */
export default function WithdrawPage() {
  /**
   * 🚨 **끝난 화면은 `AuthGate` 밖이다.** 탈퇴가 성공하면 세션이 없어지는데, 안에 두면
   *    게이트가 그 순간 `/` 로 튕겨서 "탈퇴했어요" 를 **아무도 못 본다** (실제로 그랬다).
   *    게다가 게이트가 튕기기 전에 복귀 경로로 이 주소를 기억해 둬서, 다음 로그인이
   *    탈퇴 화면으로 떨어진다. 그래서 끝 상태를 게이트 위로 올린다.
   */
  const [done, setDone] = useState(false);
  if (done) return <WithdrawDone />;

  return (
    <AuthGate>
      <WithdrawScreen onDone={() => setDone(true)} />
    </AuthGate>
  );
}

function WithdrawScreen({ onDone }: { onDone: () => void }) {
  const childId = useChildId();
  const queryClient = useQueryClient();
  const signOut = useSessionStore((s) => s.signOut);

  const [acknowledged, setAcknowledged] = useState(false);
  const [confirming, setConfirming] = useState(false);

  const withdraw = useMutation({
    mutationFn: () => {
      const body: WithdrawRequest = { acknowledged_grace_days: WITHDRAW_GRACE_DAYS };
      return api.post<WithdrawResponse>("/auth/withdraw", body);
    },
    /**
     * 🚨 성공하면 **이 기기의 세션도 버린다.** 서버가 그 보호자의 세션을 전부 무효화하므로
     *    (`docs/api/auth-kakao-v1.md` §5-3) 토큰을 들고 남으면 다음 요청이 401 이다.
     * 🚨 곧바로 `/` 로 보내지 않는다 — 무슨 일이 일어났는지 읽을 자리를 한 번 준다.
     */
    onSuccess: () => {
      setConfirming(false);
      // 🚨 **끝 화면으로 먼저 넘긴다.** `signOut()` 이 먼저면 같은 배치 안에서도 게이트가
      //    껴들 여지를 준다 — 순서를 읽는 사람에게도 "이제 게이트 밖" 이 먼저 보여야 한다.
      onDone();
      signOut();
      queryClient.clear();
    },
  });

  return (
    <Screen className="gap-6">
      <header>
        <PageTitle>탈퇴하기</PageTitle>
        <p className="text-body-sm text-ink-muted mt-2">
          읽고 나서 결정해 주세요. 되돌릴 수 있는 기간이 있지만, 그 기간이 지나면 되돌릴 수 없어요.
        </p>
      </header>

      <section className="flex flex-col gap-3">
        <h2 className="text-label text-brand">혹시 이것만 멈추고 싶으신가요</h2>
        <Card>
          <ul className="text-body-sm text-ink-muted marker:text-ink-subtle flex list-disc flex-col gap-2 pl-5">
            <li>이 기기에서만 나가고 싶다면 로그아웃으로 끝나요.</li>
            <li>위치정보 같은 선택 동의는 하나씩 끌 수 있어요.</li>
            <li>함께 보는 사람만 줄이고 싶다면 그 보호자의 연결만 끊을 수 있어요.</li>
          </ul>
          <div className="mt-3">
            <ButtonLink href={`/child/${childId}/settings`} variant="tertiary" size="compact">
              설정에서 골라 볼게요
            </ButtonLink>
          </div>
        </Card>
      </section>

      <section className="flex flex-col gap-3">
        <h2 className="text-label text-brand">탈퇴하면 이렇게 됩니다</h2>
        <Card>
          {/* 🚨 순서가 뜻을 갖는다 — 지금 일어나는 것부터 나중에 일어나는 것까지다.
              그래서 점이 아니라 `<ol>` 이다 (기호를 글자로 찍지 않는다 · 문서 §4). */}
          <ol className="text-body text-ink marker:text-ink-subtle flex list-decimal flex-col gap-3 pl-5">
            <li>
              바로 로그아웃되고, 다른 기기에 남아 있던 로그인도 함께 끊겨요.
              <span className="text-body-sm text-ink-muted mt-1 block">
                누른 그 순간부터예요. 기다리는 시간이 없어요.
              </span>
            </li>
            <li>
              함께 보던 보호자도 이 아이를 더는 볼 수 없어요.
              <span className="text-body-sm text-ink-muted mt-1 block">
                그분들에게 따로 알림이 가지는 않아요. 먼저 말씀해 주시는 편이 좋아요.
              </span>
            </li>
            <li>
              쌓인 기록은 {WITHDRAW_GRACE_DAYS}일 동안 남아 있어요.
              <span className="text-body-sm text-ink-muted mt-1 block">
                그 안에 같은 카카오 계정으로 다시 로그인하면 그대로 돌아와요.
              </span>
            </li>
            <li>
              {WITHDRAW_GRACE_DAYS}일이 지나면 지워지고, 그다음에는 되돌릴 수 없어요.
              <span className="text-body-sm text-ink-muted mt-1 block">
                아이에 대해 쌓인 기억을 처음부터 다시 만들어야 해요.
              </span>
            </li>
          </ol>
          {/* 🚨 정해지지 않은 것을 정해진 것처럼 쓰지 않는다 (약관의 같은 처리). */}
          <p className="border-line text-caption text-ink-subtle mt-4 border-t pt-4">
            무엇을 어디까지 지우는지는 아직 정리하고 있어요. 정해지면 이 안내를 먼저 고칩니다.
          </p>
        </Card>
      </section>

      <section className="flex flex-col gap-3">
        <Card>
          <Checkbox
            checked={acknowledged}
            onChange={setAcknowledged}
            label={`위 내용을 읽었고, ${WITHDRAW_GRACE_DAYS}일이 지나면 되돌릴 수 없다는 것을 알고 있어요`}
          />
        </Card>
        {/* 🚨 읽었다고 표시하기 전에는 못 누른다. 비활성은 브랜드색을 흐리게 만드는 것이
            아니라 `surface-muted` 다 (문서 §2-5) — `Button` 이 이미 그렇게 한다. */}
        <Button variant="danger" block disabled={!acknowledged} onClick={() => setConfirming(true)}>
          탈퇴하기
        </Button>
        <ButtonLink href={`/child/${childId}/settings`} variant="tertiary" block>
          그만두고 돌아가기
        </ButtonLink>
      </section>

      <BottomSheet
        open={confirming}
        onClose={() => {
          if (!withdraw.isPending) setConfirming(false);
        }}
        title="정말 탈퇴할까요?"
        description={`지금 로그인이 끊기고, ${WITHDRAW_GRACE_DAYS}일 뒤에는 되돌릴 수 없어요.`}
        footer={
          <div className="flex gap-2">
            <Button
              variant="tertiary"
              className="flex-1"
              disabled={withdraw.isPending}
              onClick={() => setConfirming(false)}
            >
              그대로 둘게요
            </Button>
            <Button
              variant="danger"
              className="flex-1"
              disabled={withdraw.isPending}
              onClick={() => withdraw.mutate()}
            >
              {withdraw.isPending ? <Spinner /> : null}
              탈퇴할게요
            </Button>
          </div>
        }
      >
        <ul className="text-body text-ink marker:text-ink-subtle flex list-disc flex-col gap-2 pl-5">
          <li>{WITHDRAW_GRACE_DAYS}일 안에 다시 로그인하면 되돌릴 수 있어요.</li>
          <li>그 기간이 지나면 처음부터 다시 만들어야 해요.</li>
        </ul>
        {withdraw.isError ? (
          <CardFailed className="mt-4">
            <p>탈퇴를 처리하지 못했어요. 계정은 그대로예요.</p>
            <p className="mt-1">다시 눌러 주세요.</p>
          </CardFailed>
        ) : null}
      </BottomSheet>
    </Screen>
  );
}

/**
 * 탈퇴 직후. 🚨 **`AuthGate` 밖에서 그린다** (위 주석). 🚨 **아이 스코프 화면으로 되돌리는
 *    링크를 두지 않는다** — 세션이 없어서 눌러도 `/` 로 튕긴다. 나가는 길은 처음 화면뿐이다.
 */
function WithdrawDone() {
  return (
    <Screen className="gap-6">
      <header>
        <PageTitle>탈퇴했어요</PageTitle>
        <p className="text-body text-ink-muted mt-3">그동안 적어 주신 것 고맙습니다.</p>
      </header>

      <Card>
        <p className="text-body text-ink">
          {WITHDRAW_GRACE_DAYS}일 안에 같은 카카오 계정으로 다시 로그인하면 쌓인 기록이 그대로
          돌아와요.
        </p>
        <p className="text-body-sm text-ink-muted mt-2">
          그 기간이 지나면 지워지고, 그다음에는 되돌릴 수 없어요.
        </p>
      </Card>

      <ButtonLink href="/" variant="secondary" block className="mt-auto">
        처음 화면으로
      </ButtonLink>
    </Screen>
  );
}
