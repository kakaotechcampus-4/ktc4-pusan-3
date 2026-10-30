"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
import { useLocationConsent } from "@/hooks/use-consents";
import { usePolicies } from "@/hooks/use-policies";
import { api, isApiError, qk, type ConsentResponse, type Policy } from "@/lib/api";
import { consentRequestBody } from "@/lib/consent";
import { toSeoulDateKey } from "@/lib/format";
import {
  askDeviceLocation,
  checkDeviceLocation,
  inLocationShell,
  nextPermission,
  openDeviceLocationSettings,
  type DeviceLocationPermission,
} from "@/lib/location";
import { claimLocationNudge } from "@/lib/location-nudge";

/** 휴대폰이 위치를 주지 않는 상태 — 이때만 안내가 선다. */
const OFF: ReadonlySet<DeviceLocationPermission> = new Set(["prompt", "declined", "denied"]);

/**
 * 04 대화 — 놀이 추천 아래 "위치 권한이 꺼져 있어서 근처 장소 없이 준비했어요" (#593 · #638 리뷰).
 *
 * 위치 동의는 켰는데 권한 창에서 거절했거나 휴대폰에서 꺼 둔 보호자는, 놀이 추천에 근처 장소가 왜 없는지 모른다.
 * 10 설정 안내(`LocationPermissionNote`)는 거기 들어가야 보이므로, 위치가 빠진 결과 바로 아래에서 한 번 말한다.
 *
 * - 🚨 **위치 동의를 안 한 보호자에게는 띄우지 않는다.** 동의는 켜져 있는데 권한이 꺼진 어긋남을 푸는 안내다
 * - 버튼은 둘이다. "위치 권한 허용하기"(막혔으면 "휴대폰 설정 열기")와 "위치 안 쓸래요"(위치 동의 철회).
 *   어느 쪽을 눌러도 어긋남이 풀린다
 * - 🚨 **아무것도 안 누르면 하루에 한 번까지만** (`claimLocationNudge`). 매번 뜨면 조르는 느낌이다
 * - 권한 상태만 본다 — 좌표를 읽지 않는다. 허용은 했는데 못 읽은 경우(`granted`)는 "꺼져 있어서" 가 거짓이라 띄우지 않는다
 *
 * 🚨 승인 게이트가 아니다 (`caution` 없음). 실패도 아니라 빨강을 쓰지 않는다. 철회도 되돌릴 수 있어서 확인 시트 없이
 *    바로 하고, 다시 켜는 곳(10 설정)을 말한다.
 */
export function LocationOffNote({ childId, turnId }: { childId: string; turnId: string }) {
  const consented = useLocationConsent(childId);
  const policies = usePolicies();
  const queryClient = useQueryClient();
  const [status, setStatus] = useState<DeviceLocationPermission | null>(null);
  const [shown, setShown] = useState(false);
  const [asking, setAsking] = useState(false);
  /** 눌러서 풀렸다. 동의를 철회하면 위 조건이 거짓이 되므로, 그 뒤 한 줄은 이 값으로 그린다. */
  const [resolved, setResolved] = useState<"granted" | "withdrawn" | null>(null);

  const update = useCallback((next: DeviceLocationPermission) => {
    setStatus((prev) => nextPermission(prev, next));
    if (next === "granted") setResolved("granted");
  }, []);

  // 처음 그릴 때와, 휴대폰 설정에서 돌아왔을 때 권한을 본다.
  useEffect(() => {
    if (!consented) return;
    let alive = true;
    const recheck = () => {
      void checkDeviceLocation().then((next) => {
        if (!alive) return;
        update(next);
        if (OFF.has(next))
          setShown((was) => was || claimLocationNudge(turnId, toSeoulDateKey(new Date())));
      });
    };
    recheck();
    const onVisible = () => {
      if (document.visibilityState === "visible") recheck();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      alive = false;
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [consented, turnId, update]);

  const locationPolicy = policies.data?.find((policy) => policy.scope === "location") ?? null;
  const withdraw = useMutation({
    mutationFn: (policy: Policy) =>
      api.post<ConsentResponse>("/consents", consentRequestBody(policy, "withdrawn", childId)),
    onSuccess: () => {
      setResolved("withdrawn");
      void queryClient.invalidateQueries({ queryKey: qk.consents(childId) });
    },
    // 🚨 약관 판이 바뀌었으면 다시 받아 온다 — 그대로 다시 누르면 같은 400 이다 (10 설정과 같다 · `ConsentSection`).
    onError: (cause) => {
      if (isApiError(cause, "policy_version_invalid")) void policies.refetch();
    },
  });

  if (resolved !== null && shown) {
    return (
      <p role="status" className="text-body-sm text-ink-muted">
        {resolved === "granted"
          ? "위치를 허용했어요. 다음 이야기부터 근처 장소도 찾아 드려요."
          : "위치를 쓰지 않을게요. 설정에서 언제든 다시 켤 수 있어요."}
      </p>
    );
  }
  if (!consented || !shown || status === null || !OFF.has(status)) return null;

  const shell = inLocationShell();
  const blocked = status === "denied";

  return (
    <div role="status" className="rounded-card bg-surface-muted text-ink-muted text-body-sm p-4">
      <p>위치 권한이 꺼져 있어서 근처 장소 없이 준비했어요.</p>
      {blocked && !shell ? (
        <p className="mt-1">
          브라우저 설정에서 이 사이트의 위치를 허용하면 근처 장소도 찾아 드려요.
        </p>
      ) : null}
      <div className="mt-3 flex flex-wrap gap-2">
        {blocked ? (
          shell ? (
            <Button variant="tertiary" size="compact" onClick={() => openDeviceLocationSettings()}>
              휴대폰 설정 열기
            </Button>
          ) : null
        ) : (
          <Button
            variant="tertiary"
            size="compact"
            disabled={asking}
            onClick={() => {
              setAsking(true);
              void askDeviceLocation()
                .then(update)
                .finally(() => setAsking(false));
            }}
          >
            {asking ? <Spinner /> : null}
            위치 권한 허용하기
          </Button>
        )}
        <Button
          variant="tertiary"
          size="compact"
          // 약관을 못 받았으면 어느 판을 철회하는지 모른다 — 누르지 못하게 둔다.
          disabled={locationPolicy === null || withdraw.isPending}
          onClick={() => {
            if (locationPolicy) withdraw.mutate(locationPolicy);
          }}
        >
          {withdraw.isPending ? <Spinner /> : null}
          위치 안 쓸래요
        </Button>
      </div>
      {withdraw.isError ? <p className="mt-2">바꾸지 못했어요. 위치 동의는 그대로예요.</p> : null}
    </div>
  );
}
