"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { josa } from "es-hangul";
import { UserRound } from "lucide-react";
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
  type Relation,
} from "@/lib/api";
import { formatDay } from "@/lib/format";

/**
 * 10 설정 — 함께 보는 보호자.
 *
 * 🚨 **승인 대기 칸을 만들지 않는다.** 초대 링크를 수락하면 `parent_child` 행이 바로 생긴다
 *    (계약서 §02). 없는 상태를 화면에 만들면 부모가 오지 않을 알림을 기다린다.
 *
 * 🚨 **owner 에게는 연결 끊기 버튼이 없다.** 서버도 409 `owner_required` 로 막는다 —
 *    화면이 버튼을 그렸다가 눌러야 실패를 알려주는 경로를 만들지 않는다. 이관은
 *    `child.owner_parent_id` 변경이라 이 화면의 일이 아니다 (계약서 §07).
 *
 * 🚨 **한 링크는 한 번만 쓴다.** 발행할 때마다 새 링크다 — "초대 링크" 를 한 번 만들어 두고
 *    계속 쓰는 것처럼 보이게 하지 않는다. 만료 시각을 같이 적는다.
 */

const RELATION_LABEL: Record<Relation, string> = {
  mother: "엄마",
  father: "아빠",
  grandparent: "조부모",
  sitter: "돌봄 선생님",
  other: "그 밖에",
};

export function ParentSection({
  childId,
  inviteOpen,
  onInviteOpenChange,
}: {
  childId: string;
  inviteOpen: boolean;
  onInviteOpenChange: (open: boolean) => void;
}) {
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
     *
     * ⚠️ 계약서 §08 의 요청 본문에는 `relation` 이 있고 "발행 시 지정한 relation 이
     *    수락자에게 프리필된다" 고 적혀 있다. 프론트는 **안 보낸다** — 수락 화면에서 받는
     *    쪽이 고르는 것이 맞다. 서버가 필수로 요구하면 계약을 고쳐야 한다 (#89).
     */
    mutationFn: () => {
      const body: InviteRequest = {};
      return api.post<InviteResponse>(`/children/${childId}/invites`, body);
    },
    onSuccess: (data) => setIssued(data),
  });

  const disconnect = useMutation({
    mutationFn: (parent: ChildParent) =>
      api.delete<null>(`/children/${childId}/parents/${parent.parent_id}`),
    onSuccess: () => {
      setDisconnecting(null);
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

  return (
    <div className="flex flex-col gap-2">
      <SettingsGroup>
        {rows.map((parent) => (
          <SettingsInfoRow
            key={parent.parent_id}
            icon={UserRound}
            title={parent.nickname}
            status={
              parent.role === "owner"
                ? `${RELATION_LABEL[parent.relation]} · 아이를 만든 보호자`
                : RELATION_LABEL[parent.relation]
            }
            note={`${formatDay(parent.connected_at)}부터 함께 보고 있어요`}
            action={
              parent.role === "owner" ? null : (
                <Button
                  variant="tertiary"
                  size="compact"
                  onClick={() => setDisconnecting(parent)}
                  disabled={disconnect.isPending}
                >
                  연결 끊기
                </Button>
              )
            }
          />
        ))}
      </SettingsGroup>

      <p className="text-caption text-ink-subtle">
        누가 무엇을 적었는지는 기록마다 남아요. 함께 보는 보호자도 같은 기억을 봅니다.
      </p>

      {/* 초대 — 링크를 만드는 것까지가 이 화면의 일이다. 보내는 것은 보호자가 한다. */}
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
            ? "이 링크를 받은 사람이 열면 바로 함께 보게 돼요."
            : "링크 하나를 만들어 전해 주세요. 아이와 어떤 사이인지는 받는 분이 직접 고릅니다."
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
                  void navigator.clipboard
                    .writeText(issued.invite_url)
                    .then(() => toast.show("초대 링크를 복사했어요"))
                    // 🚨 조용히 되돌아간 실패 — 토스트를 쓰는 유일한 자리다 (문서 §7).
                    .catch(() => toast.show("복사하지 못했어요. 링크를 길게 눌러 복사해 주세요"));
                }}
              >
                링크 복사
              </Button>
            </div>
          ) : (
            <Button block onClick={() => invite.mutate()} disabled={invite.isPending}>
              {invite.isPending ? <Spinner /> : null}
              {invite.isPending ? "만드는 중…" : "초대 링크 만들기"}
            </Button>
          )
        }
      >
        {issued ? (
          <div className="flex flex-col gap-3">
            {/* 🚨 링크를 잘라 보여주지 않는다. 길게 눌러 복사하는 경로가 남아야 한다. */}
            <p className="rounded-field border-line bg-surface text-body-sm text-ink border p-3 break-all">
              {issued.invite_url}
            </p>
            <ul className="text-body-sm text-ink-muted marker:text-ink-subtle flex list-disc flex-col gap-1.5 pl-5">
              <li>{formatDay(issued.expires_at)}까지 쓸 수 있어요.</li>
              <li>한 번 쓰면 그 링크는 닫혀요. 더 초대하려면 다시 만들어 주세요.</li>
            </ul>
          </div>
        ) : (
          <ul className="text-body-sm text-ink-muted marker:text-ink-subtle flex list-disc flex-col gap-1.5 pl-5">
            {/* 🚨 바로 위 설명이 한 말을 다시 적지 않는다. 시트 머리와 본문이 같은 문장을
                두 번 하면 읽는 사람이 둘 중 하나를 건너뛴다. */}
            <li>링크를 연 사람은 이 아이의 기록과 제안을 함께 보게 돼요.</li>
            <li>한 번 쓰면 그 링크는 닫혀요. 여러 명을 초대하려면 그만큼 만들어 주세요.</li>
          </ul>
        )}
      </BottomSheet>

      {/* 연결 끊기 — 되돌리려면 다시 초대해야 한다. 그 사실까지 적는다. */}
      <BottomSheet
        open={disconnecting !== null}
        onClose={() => {
          if (!disconnect.isPending) setDisconnecting(null);
        }}
        title={disconnecting ? `${josa(disconnecting.nickname, "이/가")} 더는 못 보게 할까요?` : ""}
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
                연결 끊기
              </Button>
            </div>
          ) : null
        }
      >
        {disconnecting ? (
          <div className="flex flex-col gap-4">
            <ul className="text-body text-ink marker:text-ink-subtle flex list-disc flex-col gap-2 pl-5">
              <li>이 아이의 기록과 제안을 더는 볼 수 없어요.</li>
              <li>그동안 적어 준 기록은 남아요. 누가 적었는지도 그대로예요.</li>
              <li>다시 함께 보려면 초대 링크를 새로 만들어야 해요.</li>
            </ul>
            {disconnect.isError ? (
              <CardFailed>
                <p>
                  {isApiError(disconnect.error) && disconnect.error.code === "owner_required"
                    ? "아이를 만든 보호자는 연결을 끊을 수 없어요."
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
