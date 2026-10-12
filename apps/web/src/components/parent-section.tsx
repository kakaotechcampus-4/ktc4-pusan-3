"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { josa } from "es-hangul";
import { UserRound } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { SettingsGroup, SettingsInfoRow } from "@/components/settings-row";
import { BottomSheet } from "@/components/ui/bottom-sheet";
import { Button } from "@/components/ui/button";
import { CardFailed } from "@/components/ui/card";
import { SkeletonBlock } from "@/components/ui/skeleton";
import { Spinner } from "@/components/ui/spinner";
import { useToast } from "@/components/ui/toast";
import {
  api,
  isApiError,
  qk,
  type ChildParent,
  type ChildParentsResponse,
  type InviteRequest,
  type InviteResponse,
} from "@/lib/api";
import { formatInviteCode } from "@/lib/invite-code";
import { parentDisplayName, RELATION_LABEL } from "@/lib/relation";
import { formatDay } from "@/lib/format";

/**
 * 10 설정 — 함께 보는 보호자.
 *
 * 🚨 **승인 대기 칸을 만들지 않는다.** 초대를 수락하면 `parent_child` 행이 바로 생긴다
 *    (계약서 §02). 없는 상태를 화면에 만들면 부모가 오지 않을 알림을 기다린다.
 *
 * 🚨 **owner 에게는 연결 끊기 버튼이 없다.** 서버도 409 `owner_required` 로 막는다 —
 *    화면이 버튼을 그렸다가 눌러야 실패를 알려주는 경로를 만들지 않는다. 이관은
 *    `child.owner_parent_id` 변경이라 이 화면의 일이 아니다 (계약서 §07).
 *
 * 🚨 **버튼은 보는 사람 기준이다** (#336 · 재형님 결정 10-06). owner 는 member 줄마다
 *    "연결 끊기", member 는 **자기 줄에만** "나가기" 다 — 남을 끊으면 서버가 403 `owner_only` 로
 *    막는다. role 을 모르는 동안(`viewer` 없음)에는 어느 줄에도 그리지 않는다 (초대 버튼과 같다).
 *
 * 🚨 **나가면 이 아이가 계정에서 사라진다.** 보호자당 아이가 하나라 0명이 되고, 0명이면
 *    `/start` 다 (apps/web CLAUDE.md §3 라우팅). `/me` 캐시를 지우고 가야 한다 — 낡은 캐시를
 *    들고 가면 `/start` 가 그 아이를 보고 홈으로 되돌려서 403 화면에 떨어진다.
 *
 * 🚨 **확인 창은 남는 것과 지워지는 것을 둘 다 말한다** (#336). 공동 기록과 "적은 사람" 은
 *    남고 그 보호자의 일기 · 개인 알림은 지워진다 — 남는 것만 적으면 지워지는 것을 모르고 누른다.
 *
 * 🚨 **한 코드는 한 번만 쓴다.** 발행할 때마다 새 코드다 — "초대 코드" 를 한 번 만들어 두고
 *    계속 쓰는 것처럼 보이게 하지 않는다. 만료 시각을 같이 적는다.
 *
 * 🚨 **초대 버튼은 owner 에게만 있다** (설정 화면이 그린다). 그래도 발행 실패는 시트 안에서
 *    말한다 — 낡은 `/me` 캐시로 member 가 시트를 열었을 때(`403 owner_only`)나 네트워크 실패에
 *    버튼만 다시 켜지고 아무 말이 없으면, 보호자는 눌린 줄도 모른다.
 */
export function ParentSection({
  childId,
  viewer,
  inviteOpen,
  onInviteOpenChange,
}: {
  childId: string;
  /** 보는 사람. `/me` 를 받기 전에는 `undefined` — 그동안은 끊기 버튼을 그리지 않는다. */
  viewer: { id: string; role: "owner" | "member" } | undefined;
  inviteOpen: boolean;
  onInviteOpenChange: (open: boolean) => void;
}) {
  const router = useRouter();
  const queryClient = useQueryClient();
  const toast = useToast();
  const [issued, setIssued] = useState<InviteResponse | null>(null);
  const [disconnecting, setDisconnecting] = useState<ChildParent | null>(null);

  const parents = useQuery({
    queryKey: qk.parents(childId),
    queryFn: () => api.get<ChildParentsResponse>(`/children/${childId}/parents`),
  });

  const invite = useMutation({
    /**
     * 🚨 **관계를 여기서 정하지 않는다.** 아이와 어떤 사이인지는 초대받은 사람이 자기 입으로
     *    말할 일이지, 보내는 사람이 미리 찍어 둘 값이 아니다 — 잘못 찍으면 받는 쪽이 자기
     *    프로필을 고치러 가야 하고, 그 값은 기록마다 "누가 적었나" 로 남는다.
     *    `docs/api/invite-v1.md` §3-1 이 발행 요청을 `{}` 로, 수락 요청이 `relation` 을 싣는 것으로 확정했다
     *    (#89 · #96).
     */
    mutationFn: () => {
      const body: InviteRequest = {};
      return api.post<InviteResponse>(`/children/${childId}/invites`, body);
    },
    onSuccess: (data) => setIssued(data),
  });

  const isSelf = (parent: ChildParent) => viewer !== undefined && parent.parent_id === viewer.id;
  const canDisconnect = (parent: ChildParent) =>
    viewer !== undefined &&
    parent.role !== "owner" &&
    (viewer.role === "owner" || parent.parent_id === viewer.id);

  const disconnect = useMutation({
    /**
     * 🚨 **이미 끊긴 것은 실패가 아니다.** `Idempotency-Key` 가 없어서(#336) 응답만 놓치고 다시
     *    누르면 404 `not_found` 가, 스스로 나간 뒤라면 아이 접근 확인에서 403
     *    `child_access_denied` 가 온다. 둘 다 원하던 상태라 실패 카드 대신 끝난 것으로 넘긴다.
     */
    mutationFn: async (parent: ChildParent) => {
      const leaving = isSelf(parent);
      try {
        await api.delete<null>(`/children/${childId}/parents/${parent.parent_id}`);
      } catch (error) {
        const done =
          isApiError(error, "not_found") || (leaving && isApiError(error, "child_access_denied"));
        if (!done) throw error;
      }
      return { leaving };
    },
    onSuccess: ({ leaving }) => {
      setDisconnecting(null);
      if (leaving) {
        queryClient.removeQueries({ queryKey: qk.me(), exact: true });
        router.replace("/start");
        return;
      }
      void queryClient.invalidateQueries({ queryKey: qk.parents(childId) });
    },
  });

  if (parents.isPending) return <SkeletonBlock label="보호자 목록을 불러오는 중" />;

  if (parents.isError) {
    return (
      <CardFailed>
        <p>함께 보는 보호자를 불러오지 못했어요.</p>
        <p className="mt-1">잠시 뒤에 다시 열어 주세요.</p>
      </CardFailed>
    );
  }

  const rows = parents.data.parents;
  const leaving = disconnecting !== null && isSelf(disconnecting);
  const disconnectingName = disconnecting ? parentDisplayName(disconnecting) : "";

  return (
    <div className="flex flex-col gap-2">
      <SettingsGroup>
        {rows.map((parent) => (
          <SettingsInfoRow
            key={parent.parent_id}
            icon={UserRound}
            title={parentDisplayName(parent)}
            status={parentStatus(parent)}
            note={`${formatDay(parent.connected_at)}부터 함께 보고 있어요`}
            action={
              canDisconnect(parent) ? (
                <Button
                  variant="tertiary"
                  size="compact"
                  onClick={() => {
                    disconnect.reset();
                    setDisconnecting(parent);
                  }}
                  disabled={disconnect.isPending}
                >
                  {isSelf(parent) ? "나가기" : "연결 끊기"}
                </Button>
              ) : null
            }
          />
        ))}
      </SettingsGroup>

      <p className="text-caption text-ink-subtle">
        누가 무엇을 적었는지는 기록마다 남아요. 함께 보는 보호자도 같은 기억을 봅니다.
      </p>

      {/* 초대 — 코드를 만드는 것까지가 이 화면의 일이다. 전하는 것은 보호자가 한다. */}
      <BottomSheet
        open={inviteOpen}
        onClose={() => {
          if (invite.isPending) return;
          onInviteOpenChange(false);
          setIssued(null);
          invite.reset();
        }}
        title="보호자 초대하기"
        description={
          issued
            ? "이 코드를 받은 사람이 앱에서 입력하면 바로 함께 보게 돼요."
            : "코드 하나를 만들어 전해 주세요. 아이와 어떤 사이인지는 받는 분이 직접 고릅니다."
        }
        footer={
          issued ? (
            <div className="flex gap-2">
              <Button
                variant="tertiary"
                className="flex-1"
                onClick={() => {
                  onInviteOpenChange(false);
                  setIssued(null);
                  invite.reset();
                }}
              >
                닫기
              </Button>
              <Button
                className="flex-1"
                onClick={() => {
                  // 🚨 복사는 **정규화된 값**이다. 화면의 `ABCD-1234` 를 그대로 복사하면
                  //    받는 쪽이 하이픈까지 붙여 넣는데, 그건 코드가 아니라 표시 형식이다.
                  void navigator.clipboard
                    .writeText(issued.invite_code)
                    .then(() => toast.show("초대 코드를 복사했어요"))
                    // 🚨 조용히 되돌아간 실패 — 토스트를 쓰는 유일한 자리다 (문서 §7).
                    .catch(() => toast.show("복사하지 못했어요. 코드를 길게 눌러 복사해 주세요"));
                }}
              >
                코드 복사
              </Button>
            </div>
          ) : (
            <Button block onClick={() => invite.mutate()} disabled={invite.isPending}>
              {invite.isPending ? <Spinner /> : null}
              {invite.isPending ? "만드는 중…" : "초대 코드 만들기"}
            </Button>
          )
        }
      >
        {issued ? (
          <div className="flex flex-col gap-3">
            {/* 🚨 네 자씩 끊어 크게 보여준다 — 이 코드는 **눈으로 옮겨 적는** 값이라,
                본문 크기로 늘어놓으면 받아 적다 틀린다. 그래서 링크와 달리 줄바꿈이 없다. */}
            <p className="rounded-field border-line bg-surface text-display text-ink border p-3 text-center tracking-[0.2em] tabular-nums">
              {formatInviteCode(issued.invite_code)}
            </p>
            <ul className="text-body-sm text-ink-muted marker:text-ink-subtle flex list-disc flex-col gap-1.5 pl-5">
              <li>{formatDay(issued.expires_at)}까지 쓸 수 있어요.</li>
              <li>한 번 쓰면 그 코드는 닫혀요. 더 초대하려면 다시 만들어 주세요.</li>
              <li>받는 분은 앱에서 로그인한 뒤 이 코드를 입력하면 돼요.</li>
            </ul>
          </div>
        ) : (
          <ul className="text-body-sm text-ink-muted marker:text-ink-subtle flex list-disc flex-col gap-1.5 pl-5">
            {/* 🚨 바로 위 설명이 한 말을 다시 적지 않는다. 시트 머리와 본문이 같은 문장을
                두 번 하면 읽는 사람이 둘 중 하나를 건너뛴다. */}
            <li>코드를 입력한 사람은 이 아이의 기록과 제안을 함께 보게 돼요.</li>
            <li>한 번 쓰면 그 코드는 닫혀요. 여러 명을 초대하려면 그만큼 만들어 주세요.</li>
          </ul>
        )}
        {invite.isError ? (
          <CardFailed className="mt-4">
            <p>
              {isApiError(invite.error, "owner_only")
                ? "아이를 등록한 보호자만 초대할 수 있어요."
                : "초대 코드를 만들지 못했어요. 잠시 뒤에 다시 눌러 주세요."}
            </p>
          </CardFailed>
        ) : null}
      </BottomSheet>

      {/* 연결 끊기 · 나가기 — 되돌리려면 다시 초대해야 한다. 그 사실까지 적는다. */}
      <BottomSheet
        open={disconnecting !== null}
        onClose={() => {
          if (!disconnect.isPending) setDisconnecting(null);
        }}
        title={
          disconnecting === null
            ? ""
            : leaving
              ? "함께 보기에서 나갈까요?"
              : `${josa(disconnectingName, "이/가")} 더는 못 보게 할까요?`
        }
        footer={
          disconnecting ? (
            <div className="flex gap-2">
              <Button
                variant="tertiary"
                className="flex-1"
                disabled={disconnect.isPending}
                onClick={() => setDisconnecting(null)}
              >
                그대로 둘게요
              </Button>
              <Button
                variant="danger"
                className="flex-1"
                disabled={disconnect.isPending}
                onClick={() => disconnect.mutate(disconnecting)}
              >
                {disconnect.isPending ? <Spinner /> : null}
                {leaving ? "나가기" : "연결 끊기"}
              </Button>
            </div>
          ) : null
        }
      >
        {disconnecting ? (
          <div className="flex flex-col gap-4">
            {leaving ? (
              <ul className="text-body text-ink marker:text-ink-subtle flex list-disc flex-col gap-2 pl-5">
                <li>이 아이의 기록과 제안을 더는 볼 수 없어요.</li>
                <li>내가 적은 기록은 아이에게 남고, 적은 사람으로 내 이름이 보여요.</li>
                <li>내가 쓴 일기와 일정에 걸어 둔 알림은 지워져요.</li>
                <li>다시 함께 보려면 아이를 등록한 보호자에게 초대 코드를 새로 받아야 해요.</li>
              </ul>
            ) : (
              <ul className="text-body text-ink marker:text-ink-subtle flex list-disc flex-col gap-2 pl-5">
                <li>이 아이의 기록과 제안을 더는 볼 수 없어요.</li>
                <li>그동안 적어 준 기록은 남아요. 누가 적었는지도 그대로예요.</li>
                <li>
                  {josa(disconnectingName, "이/가")} 쓴 일기와 일정에 걸어 둔 알림은 지워져요.
                </li>
                <li>다시 함께 보려면 초대 코드를 새로 만들어야 해요.</li>
              </ul>
            )}
            {disconnect.isError ? (
              <CardFailed>
                <p>
                  {isApiError(disconnect.error, "owner_required")
                    ? "아이를 만든 보호자는 연결을 끊을 수 없어요."
                    : isApiError(disconnect.error, "owner_only")
                      ? "다른 보호자의 연결은 아이를 등록한 보호자만 끊을 수 있어요."
                      : leaving
                        ? "나가지 못했어요. 지금은 그대로 함께 보고 있어요."
                        : "연결을 끊지 못했어요. 목록은 그대로예요."}
                </p>
              </CardFailed>
            ) : null}
          </div>
        ) : null}
      </BottomSheet>
    </div>
  );
}

/**
 * 줄의 두 번째 줄. 이름이 비어 관계 이름이 제목으로 섰으면 관계를 또 적지 않는다 (같은 말이 두 번 선다).
 */
function parentStatus(parent: ChildParent): string | undefined {
  const relation = parent.nickname?.trim() ? RELATION_LABEL[parent.relation] : null;
  if (parent.role === "owner") {
    return relation ? `${relation} · 아이를 만든 보호자` : "아이를 만든 보호자";
  }
  return relation ?? undefined;
}
