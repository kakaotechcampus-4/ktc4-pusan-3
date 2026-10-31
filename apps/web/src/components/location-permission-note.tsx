"use client";

import { useCallback, useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
import {
  askDeviceLocation,
  checkDeviceLocation,
  inLocationShell,
  openDeviceLocationSettings,
  type DeviceLocationPermission,
} from "@/lib/location";

/**
 * 10 설정 — 위치 동의는 켜져 있는데 **휴대폰이 위치를 허용하지 않은** 때의 안내 (#593).
 *
 * 동의(서버 기록)와 휴대폰 권한(OS)은 따로 움직인다. 동의만 켜져 있으면 입력에 위치가 실리지 않는데,
 * 보호자는 동의 줄의 "동의함" 만 보고 근처 장소를 찾아 줄 거라 믿는다. 그래서 둘이 어긋날 때만 한 줄로 말한다.
 *
 * - 다시 물을 수 있으면(`prompt` · `declined`) **다시 묻는 버튼**을 둔다
 * - 더 물을 수 없으면(`denied`) 설정에서 켜는 길을 말한다. 앱 안이면 설정 화면을 여는 버튼까지
 * - 허용했거나(`granted`) 위치를 읽을 길이 없으면(`unsupported`) 아무것도 그리지 않는다
 *
 * 🚨 **승인 게이트가 아니다** — `caution` 을 쓰지 않는다. 🚨 실패도 아니라 빨강을 쓰지 않는다.
 * 🚨 이 안내는 **좌표를 읽지 않는다** — 권한 상태만 본다 (`checkDeviceLocation`).
 * ⚠️ 상자는 `CardFailed` 와 같은 `surface-muted` 바탕이다. 실패가 아니라 그 컴포넌트를 쓰지 않았고 카드 변형을 늘리지도
 *    않았다 — 위치가 실리지 않는 상태를 말하는 자리라 같은 차분한 톤이 맞다. `card-failed` 사양이 바뀌면 같이 본다.
 */
export function LocationPermissionNote({
  status,
  onStatus,
}: {
  /** `null` 이면 아직 모른다 — 아무것도 그리지 않는다. 동의를 켠 직후의 권한 창 결과가 여기로 온다. */
  status: DeviceLocationPermission | null;
  onStatus: (status: DeviceLocationPermission) => void;
}) {
  const [asking, setAsking] = useState(false);

  const recheck = useCallback(() => {
    void checkDeviceLocation().then(onStatus);
  }, [onStatus]);

  // 처음 열 때와, 휴대폰 설정에서 돌아왔을 때 다시 본다.
  useEffect(() => {
    recheck();
    const onVisible = () => {
      if (document.visibilityState === "visible") recheck();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => document.removeEventListener("visibilitychange", onVisible);
  }, [recheck]);

  if (status === null || status === "granted" || status === "unsupported") return null;

  const shell = inLocationShell();
  const place = shell ? "휴대폰" : "브라우저";

  if (status === "denied") {
    return (
      <div role="status" className="rounded-card bg-surface-muted text-ink-muted text-body-sm p-4">
        <p>
          {shell
            ? "휴대폰 설정에서 아이캐치의 위치 권한을 허용해 주세요. 허용하면 다음 입력부터 근처 장소를 찾아 드려요."
            : "브라우저 설정에서 이 사이트의 위치 권한을 허용해 주세요. 허용하면 다음 입력부터 근처 장소를 찾아 드려요."}
        </p>
        {shell ? (
          <Button
            variant="tertiary"
            size="compact"
            className="mt-3"
            onClick={() => openDeviceLocationSettings()}
          >
            휴대폰 설정 열기
          </Button>
        ) : null}
      </div>
    );
  }

  return (
    <div role="status" className="rounded-card bg-surface-muted text-ink-muted text-body-sm p-4">
      <p>
        {status === "declined"
          ? `${place}에서 위치를 허용하지 않았어요. 허용해야 근처 장소를 찾아 드릴 수 있어요.`
          : `${place}의 위치 권한이 꺼져 있어서 아직 위치를 보내지 않고 있어요.`}
      </p>
      <Button
        variant="tertiary"
        size="compact"
        className="mt-3"
        disabled={asking}
        onClick={() => {
          setAsking(true);
          void askDeviceLocation()
            .then(onStatus)
            .finally(() => setAsking(false));
        }}
      >
        {asking ? <Spinner /> : null}
        위치 권한 허용하기
      </Button>
    </div>
  );
}
