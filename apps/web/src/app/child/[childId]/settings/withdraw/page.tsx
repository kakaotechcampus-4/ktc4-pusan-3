"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft } from "lucide-react";
import { useState } from "react";

import { AuthGate } from "@/components/auth-gate";
import { BottomSheet } from "@/components/ui/bottom-sheet";
import { Button, ButtonLink } from "@/components/ui/button";
import { Card, CardFailed } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { ICON_SIZE, ICON_STROKE } from "@/components/ui/icon";
import { IconButtonLink } from "@/components/ui/icon-button";
import { PageTitle } from "@/components/ui/page-title";
import { Screen } from "@/components/ui/screen";
import { Spinner } from "@/components/ui/spinner";
import { useChildId } from "@/hooks/use-child-id";
import { api, ApiError, type WithdrawRequest, type WithdrawResponse } from "@/lib/api";
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
 * 🚨 **유예가 없다 — 누르면 그 자리에서 지워진다** (PM 09-29 · #167). 삭제 정책 정본 §9 가
 *    MVP 를 복구 없는 hard delete 로 정해 뒀다. 그래서 이 화면에 **"며칠 안에 다시 로그인하면
 *    돌아와요" 를 다시 세우지 않는다** — 지운 뒤에 돌아올 길을 약속하는 문구가 된다.
 *    같은 이유로 ㉡ 읽음 표시와 ㉢ 확인 시트를 **줄이지 않는다**: 되돌릴 장치가 없어서
 *    실수를 막는 것이 그 둘뿐이다.
 *
 * ⚠️ `POST /auth/withdraw` 는 **계약서에 없다** (`lib/api/types.ts`). 지금은 MSW 목 위에서만 돈다.
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
      const body: WithdrawRequest = { acknowledged_immediate_deletion: true };
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
        {/* 🚨 **아래 "그만두고 돌아가기" 와 중복이 아니다.** 이 화면은 스크롤이 길어서,
            읽다 그만두려는 사람이 아래까지 내려가야 나가는 길을 만나면 안 된다.
            둘 다 같은 곳으로 간다 — 나가는 길을 두 뜻으로 가르지 않는다. */}
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
          <PageTitle>탈퇴하기</PageTitle>
        </div>
        <p className="text-body-sm text-ink-muted mt-2">
          읽고 나서 결정해 주세요. 누르면 바로 지워지고, 되돌릴 수 없어요.
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
          {/* 🚨 **`<ol>` 이 아니다.** 번호는 "지금 → 며칠 뒤" 라는 순서에서 왔는데, 유예가
              없어지면서 적힌 일이 전부 **같은 순간에** 일어난다 (#167) — 번호를 남기면 화면이
              아직 단계가 있는 것처럼 말한다. 순서 없는 목록은 점으로 찍는다 (문서 §4).
              🚨 **가장 무거운 결과가 맨 위다.** 로그아웃부터 읽히면 아이 기록이 지워진다는
              말이 세 번째 줄에서 나온다 — 읽다 마는 사람이 그것을 못 보고 나간다. */}
          <ul className="text-body text-ink marker:text-ink-subtle flex list-disc flex-col gap-3 pl-5">
            {/* 🚨 **등록 보호자와 함께 보는 보호자가 서로 다른 일을 겪는다** (삭제 정책 정본
                §9 · #182 리뷰). 한 줄로 합쳐 "기록이 지워져요" 라고만 쓰면, 초대로 들어온
                보호자에게는 **일어나지 않는 일**을 예고하는 화면이 된다. 서버가 붙는 날
                바로 생기는 경우라(초대 화면은 이미 있다) 지금 두 줄로 갈라 둔다.
                // 넘기기(이관)가 생기면 여기서 먼저 권한다 — 약관 제9조 ③ */}
            <li>
              아이를 등록한 보호자라면, 아이와 쌓인 기록이 함께 지워져요.
              <span className="text-body-sm text-ink-muted mt-1 block">
                함께 보던 보호자도 이 아이를 더는 볼 수 없게 돼요. 그분들에게 따로 알림이 가지는
                않으니 먼저 말씀해 주시는 편이 좋아요.
              </span>
            </li>
            <li>
              함께 보는 보호자라면, 공동 기록은 아이에게 남고 쓴 사람 이름만 사라져요.
              <span className="text-body-sm text-ink-muted mt-1 block">
                아이와 등록 보호자 쪽은 그대로예요. 대신 내가 이 아이를 보는 연결이 끊겨요.
              </span>
            </li>
            <li>
              바로 로그아웃되고, 다른 기기에 남아 있던 로그인도 함께 끊겨요.
              <span className="text-body-sm text-ink-muted mt-1 block">
                누른 그 순간부터예요. 기다리는 시간이 없어요.
              </span>
            </li>
            <li>
              지워진 것은 되돌릴 수 없어요.
              <span className="text-body-sm text-ink-muted mt-1 block">
                다시 로그인해도 돌아오지 않아요. 계정을 새로 만들고, 아이도 다시 등록하거나 초대를
                다시 받아야 해요.
              </span>
            </li>
          </ul>
          {/* 🚨 정해지지 않은 것을 정해진 것처럼 쓰지 않는다 (약관의 같은 처리).
              🚨 **여기에 "한 톨도 남지 않아요" 를 쓰지 않는다.** 동의문 1 이 백업 사본을
                 최대 30일, 약관 제9조 ② 가 동의 기록을 1년 보관한다고 약속한다 (#182 리뷰) —
                 그 API 가 생기는 날 이 문단을 그 숫자에 맞춘다. 지금 "전부 지워져요" 로
                 단정하면 그때 화면이 약관과 어긋난 채로 남는다. */}
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
            label="위 내용을 읽었고, 지금 지워지고 되돌릴 수 없다는 것을 알고 있어요"
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
        description="지금 지워지고 로그인도 끊겨요. 되돌릴 수 없어요."
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
          <li>아이를 등록한 보호자라면 아이와 쌓인 기록이 함께 지워져요.</li>
          <li>함께 보는 보호자라면 공동 기록은 아이에게 남고, 이 아이를 보는 연결이 끊겨요.</li>
          <li>되살릴 방법이 없어요. 다시 쓰시려면 처음부터 다시 만들어야 해요.</li>
        </ul>
        {withdraw.isError ? <WithdrawFailed error={withdraw.error} /> : null}
      </BottomSheet>
    </Screen>
  );
}

/**
 * 확정 시트의 실패 자리.
 *
 * 🚨 **서버가 거절한 것과 응답을 못 받은 것을 같은 말로 하지 않는다** (#182 리뷰).
 *    유예가 없어서 요청이 서버에 닿았다면 그 순간 지워지는데(#167), 연결이 끊겨 응답만
 *    못 받은 경우에 "계정은 그대로예요" 라고 하면 **이미 지워진 사람에게 거짓말**이 된다.
 *
 * 가르는 선은 **서버가 답을 줬는가**다. 4xx 는 서버가 요청을 받고 거절한 것이라 계정이
 * 그대로지만, 5xx · 네트워크 실패는 무엇이 저장됐는지 모르는 상태다 —
 * `useRunStream` 이 `unconfirmed` 를 따로 두는 것과 같은 구분이다.
 *
 * 🚨 **실패를 빨강으로 칠하지 않는다** (디자인 시스템 §7). `CardFailed` 가 중립 면이다.
 */
function WithdrawFailed({ error }: { error: unknown }) {
  const refusedByServer = error instanceof ApiError && error.status < 500;

  if (refusedByServer) {
    return (
      <CardFailed className="mt-4">
        <p>탈퇴를 처리하지 못했어요. 계정은 그대로예요.</p>
        <p className="mt-1">다시 눌러 주세요.</p>
      </CardFailed>
    );
  }

  return (
    <CardFailed className="mt-4">
      <p>처리됐는지 확인하지 못했어요.</p>
      <p className="mt-1">
        연결이 끊겨서 지워졌는지 알 수 없어요. 다시 눌러 주세요. 이미 지워졌다면 처음 화면으로
        돌아가요.
      </p>
    </CardFailed>
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

      {/* 🚨 **다시 올 길을 여기서 약속하지 않는다.** 이미 지워진 뒤라, "다시 로그인하면" 으로
          시작하는 문장은 무엇이든 없는 것을 가리킨다 (#167). 같은 카카오 계정으로 다시
          시작할 수는 있지만 그건 **새 계정**이고, 그 말은 처음 화면이 할 말이다. */}
      <Card>
        <p className="text-body text-ink">계정이 지워졌어요. 되돌릴 수 없어요.</p>
        {/* 🚨 **역할을 모른 채 "기록이 지워졌어요" 라고 단정하지 않는다** (#182 리뷰).
            함께 보는 보호자였다면 공동 기록은 아이에게 남는다 — 이 화면은 세션이 이미
            없어서 물어볼 곳도 없다. 그래서 두 경우를 **그대로 적는다.** */}
        <p className="text-body-sm text-ink-muted mt-2">
          아이를 등록한 보호자였다면 아이와 쌓인 기록도 함께 지워졌고, 함께 보는 보호자였다면 공동
          기록은 아이에게 남아요.
        </p>
      </Card>

      <ButtonLink href="/" variant="secondary" block className="mt-auto">
        처음 화면으로
      </ButtonLink>
    </Screen>
  );
}
