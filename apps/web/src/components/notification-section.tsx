"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bell, BellOff } from "lucide-react";
import { useState } from "react";

import { SettingsGroup, SettingsInfoRow, SettingsRowSkeleton } from "@/components/settings-row";
import { Button } from "@/components/ui/button";
import { CardFailed } from "@/components/ui/card";
import { SkeletonGroup } from "@/components/ui/skeleton";
import { Spinner } from "@/components/ui/spinner";
import { api, qk, type NotificationSettings } from "@/lib/api";

/**
 * 10 설정 › 알림 — "일정 알림" 을 켜고 끈다 (#609 · 서버 #605).
 *
 * 🚨 **보호자 단위다.** 아이마다가 아니고 내 모든 기기에 함께 적용된다. 다른 보호자에게는 번지지 않는다.
 * 🚨 **끄면 발송만 멈춘다.** 일정마다 걸어 둔 알림은 남는다 — 줄 아래 한 줄이 그 사실을 말한다.
 *    끄면 알림이 지워지는 줄 알면, 다시 켰을 때 하나하나 다시 걸어야 하는 줄 안다.
 *
 * 🚨 **스위치가 아니라 동의 줄과 같은 모양이다** (`ConsentSection`). 이 화면은 줄마다 **지금 상태를 글자로**
 *    달고 오른쪽 버튼 하나가 행동을 든다 (`settings-row.tsx`). 이 구역만 토글을 세우면 한 화면에 켜고 끄는
 *    모양이 둘이 된다. 확인 시트는 두지 않는다 — 다시 누르면 되돌아가는 개인 설정이라서다.
 * 🚨 **낙관적으로 바꾸지 않는다.** 버튼 하나를 누르고 응답을 기다리는 동안 그 버튼만 잠긴다. 상태 글자가
 *    서버 값보다 먼저 바뀌면 "켜짐" 을 보고 나간 보호자가 저장 실패를 모른다.
 *
 * 🚨 **복약 알림 줄은 그리지 않는다.** 서버에 칸은 있지만 복약 알림 발송이 아직 없어서 (#594 범위 밖),
 *    켜도 아무 일이 없는 줄이 된다. 발송이 붙으면 같은 모양의 줄을 하나 더한다.
 *    `PUT` 은 두 칸을 함께 보내야 해서 그 칸은 받은 값을 그대로 싣는다.
 * 🚨 **승인 게이트가 아니다** — Idempotency-Key 도, `btn-approve` 도 없다 (최상위 CLAUDE.md §2).
 */
export function NotificationSection() {
  const queryClient = useQueryClient();
  /** 값이 바뀐 뒤 한 문장. 자리를 차지하지 않고 소리로만 남는다 (`ConsentSection` 과 같다). */
  const [announcement, setAnnouncement] = useState("");

  const settings = useQuery({
    queryKey: qk.notificationSettings(),
    queryFn: () => api.get<NotificationSettings>("/me/notification-settings"),
  });

  const change = useMutation({
    mutationFn: (next: NotificationSettings) =>
      api.put<NotificationSettings>("/me/notification-settings", next),
    onSuccess: (saved) => {
      queryClient.setQueryData(qk.notificationSettings(), saved);
      setAnnouncement(saved.schedule_enabled ? "일정 알림을 켰어요." : "일정 알림을 껐어요.");
    },
    // 🚨 실패해도 서버가 저장했을 수 있다(응답만 잃은 경우). 화면이 "그대로" 라고 단정하지 않고 다시 받는다.
    onError: () => {
      void queryClient.invalidateQueries({ queryKey: qk.notificationSettings() });
    },
  });

  // 🚨 불러온 뒤와 같은 모양으로 기다린다 — 줄 하나에 상태와 안내 한 줄 (`SettingsRowSkeleton`).
  if (settings.isPending) {
    return (
      <SkeletonGroup label="알림 설정을 불러오는 중">
        <SettingsGroup>
          <SettingsRowSkeleton note />
        </SettingsGroup>
      </SkeletonGroup>
    );
  }

  if (settings.isError) {
    return (
      <CardFailed>
        <p>알림 설정을 불러오지 못했어요.</p>
        <p className="mt-1">잠시 뒤에 다시 열어 주세요.</p>
      </CardFailed>
    );
  }

  const on = settings.data.schedule_enabled;

  return (
    <div className="flex flex-col gap-3">
      <p aria-live="polite" className="sr-only">
        {announcement}
      </p>

      <SettingsGroup>
        <SettingsInfoRow
          icon={on ? Bell : BellOff}
          title="일정 알림"
          status={on ? "켜짐" : "꺼짐"}
          note={
            on
              ? "일정마다 고른 시각에 알려 드려요."
              : "걸어 둔 알림은 남아 있어요. 다시 켜면 아직 오지 않은 알림부터 울려요."
          }
          action={
            <Button
              variant="tertiary"
              size="compact"
              disabled={change.isPending}
              onClick={() => change.mutate({ ...settings.data, schedule_enabled: !on })}
            >
              {change.isPending ? <Spinner /> : null}
              {on ? "끄기" : "켜기"}
            </Button>
          }
        />
      </SettingsGroup>

      {/* 🚨 실패를 빨강으로 칠하지 않는다 (디자인 시스템 §3). 위 줄은 다시 받은 서버 값이다. */}
      {change.isError ? (
        <CardFailed>
          <p>알림 설정을 바꾸지 못했어요.</p>
          <p className="mt-1">위 상태를 확인하고 잠시 뒤에 다시 눌러 주세요.</p>
        </CardFailed>
      ) : null}
    </div>
  );
}
