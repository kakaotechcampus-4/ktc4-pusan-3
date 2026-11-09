"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { AuthGate } from "@/components/auth-gate";
import { ScreenTransition } from "@/components/route-transition";
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
import {
  api,
  ApiError,
  deleteChild,
  isApiError,
  qk,
  type ChildProfile,
  type IdempotencyKey,
  type Me,
} from "@/lib/api";
import { useIdempotencyKey } from "@/lib/api/use-idempotency-key";
import { useSessionStore } from "@/stores/session";
import { BACK } from "@/lib/view-transition";

/**
 * 11 아이 프로필 › 아이 삭제 — **흐름 중인 화면이다** (#751).
 *
 * 아이 기본정보 · 건강정보 동의를 앱 안에서 거두는 길이다 (개인정보 보호법 제38조 ④ — 거두는 방법이
 * 모을 때보다 어렵지 않아야 한다). 거두면 아이와 기록을 통째로 지운다 (10-14 PM 결정 — 건강정보 동의
 * 없이는 입력부터 막혀서 골라 지우지 않는다).
 *
 * 🚨 **탈퇴 화면(`settings/withdraw`)과 같은 세 단계다** — ㉠ 무엇이 일어나는지 읽기 ㉡ 읽었다고 표시하기
 *    ㉢ 마지막 확인 시트. 유예가 없고 되돌릴 수 없어서, 실수를 막는 것이 ㉡ 과 ㉢ 뿐이다. 줄이지 않는다.
 * 🚨 **하단 네비를 붙이지 않는다.** 고르는 도중에 새는 길을 만들면 그 화면이 끝나지 않는다
 *    (`apps/web/CLAUDE.md` §3). 빠져나가는 길은 "그만두고 돌아가기" 와 위의 화살표다 — 둘 다 같은 곳이다.
 * 🚨 **승인 게이트가 아니다.** 보호자가 직접 누르는 삭제다 (최상위 §2 — 게이트는 캘린더 쓰기 · 건강 기록
 *    확정 딱 2곳). `btn-approve` 도 `caution` 색도 쓰지 않는다. 파괴적 확정이라 확정 버튼만 `danger` 다.
 * 🚨 **등록 보호자만 지운다.** 프로필은 함께 보는 보호자에게 이 화면으로 오는 줄을 그리지 않지만, 주소로
 *    바로 들어올 수는 있다 — 그때는 버튼 대신 누가 지울 수 있는지를 말한다.
 *
 * 계약은 서버 Swagger 가 정본이다 (`DELETE /children/{cid}` · #751).
 */
export default function DeleteChildPage() {
  return (
    <AuthGate>
      <ScreenTransition>
        <DeleteChildScreen />
      </ScreenTransition>
    </AuthGate>
  );
}

function DeleteChildScreen() {
  const childId = useChildId();
  const router = useRouter();
  const queryClient = useQueryClient();
  const setActiveChild = useSessionStore((s) => s.setActiveChild);
  const idem = useIdempotencyKey();

  const [acknowledged, setAcknowledged] = useState(false);
  const [confirming, setConfirming] = useState(false);

  const profile = useQuery({
    queryKey: qk.childProfile(childId),
    queryFn: () => api.get<ChildProfile>(`/children/${childId}`),
  });

  const remove = useMutation({
    mutationFn: () => requestDeleteChild(childId, idem.current()),
    /**
     * 🚨 **`/start` 로 보낸다** — 다른 아이가 있으면 그 아이 홈으로, 없으면 경로 고르기로 보내는 판단이
     *    거기 있다. 함께 보기에서 나가기(`ParentSection`)와 같은 길이다.
     * 🚨 **`/me` 를 버리고 간다.** 들고 가면 `/start` 가 지운 아이 홈으로 되돌린다.
     * 🚨 "마지막에 본 아이" 가 지운 아이면 비운다 — 다음에 앱을 켤 때 첫 화면이 없는 아이를 고르지 않게.
     */
    onSuccess: () => {
      idem.rotate();
      setConfirming(false);
      if (useSessionStore.getState().activeChildId === childId) setActiveChild(null);
      queryClient.removeQueries({ queryKey: qk.me(), exact: true });
      router.replace("/start");
    },
  });

  /**
   * 🚨 **닫을 때 지난 실패를 지운다.** 남겨 두면 나중에 다시 열었을 때 누르기도 전에 옛 실패 문구가 선다.
   *    키는 그대로 둔다(`rotate` 하지 않는다) — 응답을 못 받은 앞의 요청이 지웠을 수 있어 다시 누르면 재시도다.
   */
  const closeSheet = () => {
    if (remove.isPending) return;
    setConfirming(false);
    remove.reset();
  };

  return (
    <Screen className="gap-6">
      <header>
        {/* 🚨 **화살표는 링크다** (`IconButtonLink`) — 탈퇴 화면과 같은 이유. `router.back()` 은 어디서
            왔는지에 따라 달라져서, 링크로 바로 들어오면 돌아갈 데가 없다. */}
        <div className="-ml-3 flex items-center gap-1">
          <IconButtonLink
            href={`/child/${childId}/profile`}
            label="아이 프로필로 돌아가기"
            {...BACK}
          >
            <ArrowLeft aria-hidden size={ICON_SIZE.md} strokeWidth={ICON_STROKE} />
          </IconButtonLink>
          <PageTitle>아이 삭제하기</PageTitle>
        </div>
        <p className="text-body-sm text-ink-muted mt-2">
          읽고 나서 결정해 주세요. 누르면 바로 지워지고, 되돌릴 수 없어요.
        </p>
      </header>

      {profile.isPending ? (
        <p className="text-body text-ink-muted flex items-center gap-2">
          <Spinner />
          불러오는 중…
        </p>
      ) : profile.isError ? (
        <CardFailed>
          <p>아이 정보를 불러오지 못했어요.</p>
          <p className="mt-1">잠시 뒤에 다시 열어 주세요.</p>
        </CardFailed>
      ) : profile.data.role !== "owner" ? (
        <Card>
          <p className="text-body text-ink">아이를 등록한 보호자만 아이를 삭제할 수 있어요.</p>
          <p className="text-body-sm text-ink-muted mt-2">
            함께 보기를 그만두고 싶다면 설정의 함께 보는 보호자에서 나갈 수 있어요.
          </p>
        </Card>
      ) : (
        <>
          <section className="flex flex-col gap-3">
            <h2 className="text-label text-brand">혹시 이것만 멈추고 싶으신가요</h2>
            <Card>
              <ul className="text-body-sm text-ink-muted marker:text-ink-subtle flex list-disc flex-col gap-2 pl-5">
                <li>기록 하나만 지우고 싶다면 기록/기억 탭에서 그 기록만 지울 수 있어요.</li>
                <li>위치정보 같은 선택 동의는 설정에서 하나씩 끌 수 있어요.</li>
                <li>함께 보는 사람만 줄이고 싶다면 설정에서 그 보호자의 연결만 끊을 수 있어요.</li>
              </ul>
            </Card>
          </section>

          <section className="flex flex-col gap-3">
            <h2 className="text-label text-brand">삭제하면 이렇게 돼요</h2>
            <Card>
              {/* 🚨 **가장 무거운 결과가 맨 위다** — 탈퇴 화면과 같은 이유. 읽다 마는 사람이 기록이
                  지워진다는 말을 못 보고 나가지 않게. 전부 같은 순간에 일어나서 번호를 달지 않는다. */}
              <ul className="text-body text-ink marker:text-ink-subtle flex list-disc flex-col gap-3 pl-5">
                <li>
                  아이 기본정보 · 건강정보 동의를 거두는 것과 같아요. 아이와 쌓인 기록이 모두
                  지워져요.
                  <span className="text-body-sm text-ink-muted mt-1 block">
                    기록 · 일정 · 사진 · 대화 · 일기와 알레르기 · 건강 정보까지 전부예요.
                  </span>
                </li>
                <li>
                  함께 보던 보호자도 이 아이를 더는 볼 수 없게 돼요.
                  <span className="text-body-sm text-ink-muted mt-1 block">
                    그분들에게 따로 알림이 가지는 않으니 먼저 말씀해 주시는 편이 좋아요.
                  </span>
                </li>
                <li>
                  내 계정은 그대로예요.
                  <span className="text-body-sm text-ink-muted mt-1 block">
                    로그인도 그대로예요. 다른 아이가 있으면 그 아이 화면으로, 없으면 아이를 새로
                    등록하거나 초대 코드로 참여하는 화면으로 가요.
                  </span>
                </li>
                <li>
                  지워진 것은 되돌릴 수 없어요.
                  <span className="text-body-sm text-ink-muted mt-1 block">
                    같은 아이를 다시 등록해도 지운 기록은 돌아오지 않아요.
                  </span>
                </li>
              </ul>
              {/* 🚨 **"한 톨도 남지 않아요" 를 쓰지 않는다** — 탈퇴 화면과 같다. 서버는 동의 증빙을 보관
                  표로 옮긴 뒤 지운다(`purge_child`). 처리방침 1장의 "동의 증빙 1년" 이다. */}
              <p className="border-line text-caption text-ink-subtle mt-4 border-t pt-4">
                언제 무엇에 동의했는지 남긴 기록은 약관에 따라 1년 동안 따로 보관했다가 파기해요.
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
            <Button
              variant="danger"
              block
              disabled={!acknowledged}
              onClick={() => setConfirming(true)}
            >
              아이 삭제하기
            </Button>
            <ButtonLink href={`/child/${childId}/profile`} variant="tertiary" block {...BACK}>
              그만두고 돌아가기
            </ButtonLink>
          </section>
        </>
      )}

      <BottomSheet
        open={confirming}
        onClose={closeSheet}
        title="정말 이 아이를 삭제할까요?"
        description="아이 기본정보 · 건강정보 동의를 거두는 것과 같아요. 기록이 모두 지워지고 되돌릴 수 없어요."
        footer={
          <div className="flex gap-2">
            <Button
              variant="tertiary"
              className="flex-1"
              disabled={remove.isPending}
              onClick={closeSheet}
            >
              그대로 둘게요
            </Button>
            <Button
              variant="danger"
              className="flex-1"
              disabled={remove.isPending}
              onClick={() => remove.mutate()}
            >
              {remove.isPending ? <Spinner /> : null}
              삭제할게요
            </Button>
          </div>
        }
      >
        <ul className="text-body text-ink marker:text-ink-subtle flex list-disc flex-col gap-2 pl-5">
          <li>함께 보던 보호자도 이 아이를 더는 볼 수 없게 돼요.</li>
          <li>내 계정은 그대로예요.</li>
        </ul>
        {remove.isError ? <DeleteChildFailed error={remove.error} /> : null}
      </BottomSheet>
    </Screen>
  );
}

/**
 * 응답을 못 받아 `/me` 를 불렀더니 아이가 아직 있었다 — **그 순간에는** 지워지지 않았다.
 *
 * 🚨 "아이는 그대로예요" 로 단정하지 않는다 — 탈퇴 화면의 `WithdrawNotApplied` 와 같은 이유다. 브라우저만
 *    포기하고 서버는 아직 지우는 중일 수 있다. 그래서 "아직" 이고 다시 누르게 한다 — 다시 보낸 요청은 앞
 *    요청이 끝날 때까지 아이 잠금에서 기다렸다가, 같은 키면 204, 이미 끝났으면 403 으로 결말이 난다.
 */
class ChildDeleteNotApplied extends Error {
  constructor() {
    super("아이 삭제가 처리되지 않았다");
    this.name = "ChildDeleteNotApplied";
  }
}

/**
 * 아이 삭제를 보내고, 끝났는지 모르면 한 번 확인한다. 돌아오면 지워진 것이다.
 *
 * 🚨 **403 `child_access_denied` 는 지워졌다는 뜻이다** (서버 #751). 끝난 뒤 같은 키로 다시 보내면 재생이
 *    아니라 이 403 이다 — 아이 접근 확인이 키 재생보다 먼저다 (`docs/api/idempotency-v1.md` §3-1).
 *    등록 보호자는 연결을 끊을 수 없어서(409 `owner_required`) 이 코드가 오는 길은 아이가 없어진 것뿐이다.
 * 🚨 **응답을 못 받았으면 `/me` 로 한 번 확인한다.** 목록에 아이가 없으면 끝났고, 있으면 아직이고, 그것도
 *    못 받으면 모르는 채로 둔다 — 그때 다시 누르는 요청이 위의 길로 결말을 낸다.
 * 🚨 4xx(403 `owner_only` · 400) 는 서버가 받고 거절한 것이라 아무것도 지워지지 않았다 — 확인하지 않는다.
 */
async function requestDeleteChild(childId: string, key: IdempotencyKey): Promise<void> {
  try {
    await deleteChild(childId, key);
    return;
  } catch (error) {
    if (isApiError(error, "child_access_denied")) return;
    if (error instanceof ApiError && error.status < 500) throw error;

    let me: Me;
    try {
      me = await api.get<Me>("/me");
    } catch {
      throw error;
    }
    if (!me.children.some((c) => c.child_id === childId)) return;
    throw new ChildDeleteNotApplied();
  }
}

/**
 * 확정 시트의 실패 자리 — 탈퇴 화면의 `WithdrawFailed` 와 같은 세 갈래다.
 *
 * 🚨 **서버가 거절한 것과 응답을 못 받은 것을 같은 말로 하지 않는다.** 응답만 못 받은 경우에 "아이는
 *    그대로예요" 라고 하면 이미 지워진 사람에게 거짓말이 된다.
 * 🚨 **실패를 빨강으로 칠하지 않는다** (디자인 시스템 §7). `CardFailed` 가 중립 면이다.
 */
function DeleteChildFailed({ error }: { error: unknown }) {
  if (error instanceof ChildDeleteNotApplied) {
    return (
      <CardFailed className="mt-4">
        <p>확인해 보니 아직 지워지지 않았어요.</p>
        <p className="mt-1">다시 눌러 주세요. 그 사이에 지워졌다면 다음 화면으로 넘어가요.</p>
      </CardFailed>
    );
  }

  if (error instanceof ApiError && error.status < 500) {
    return (
      <CardFailed className="mt-4">
        <p>삭제하지 못했어요. 아이와 기록은 그대로예요.</p>
        <p className="mt-1">다시 눌러 주세요.</p>
      </CardFailed>
    );
  }

  return (
    <CardFailed className="mt-4">
      <p>처리됐는지 확인하지 못했어요.</p>
      <p className="mt-1">
        연결이 끊겨서 지워졌는지 알 수 없어요. 다시 눌러 주세요. 이미 지워졌다면 다음 화면으로
        넘어가요.
      </p>
    </CardFailed>
  );
}
