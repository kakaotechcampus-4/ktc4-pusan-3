"use client";

import { useQuery } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardFailed } from "@/components/ui/card";
import { PageTitle } from "@/components/ui/page-title";
import { Screen } from "@/components/ui/screen";
import { api, qk, type AuthStatus } from "@/lib/api";
import { OAuthUnavailableError, startOAuthLogin } from "@/lib/auth/oauth";

/**
 * 00 소개 · 로그인 — 프로토타입에 없는 화면이다.
 *
 * 소개는 일부러 짧게 뒀다. 여기서 서비스를 설명해 설득하는 게 아니라,
 * "무엇을 모으고 무엇을 안 하는지" 만 먼저 말하고 로그인으로 보낸다. (자세한 소개는 다음 이슈)
 *
 * 🚨 로그인 성공 처리는 여기 없다. 로그인은 페이지 이동이라 이 화면은 떠나고,
 *    돌아오는 곳은 `/auth/callback` 이다 (docs/web/kakao-login-v1.md §4-4).
 */

const POINTS = [
  "말하듯 한 줄만 남기면 AI 가 아이 기억으로 정리해요.",
  "식사 · 놀이 · 교육 · 건강, 지금 맞는 다음 행동을 준비해 둬요.",
  "쌓인 기억을 근거로 보여주고, 근거가 없으면 없다고 말해요.",
];

export default function LoginPage() {
  const router = useRouter();

  // 버튼을 누른 뒤 조회하면 이동 전에 왕복이 한 번 낀다. 그래서 진입 시 미리 받아 둔다.
  const status = useQuery({
    queryKey: qk.authStatus("kakao"),
    queryFn: () => api.get<AuthStatus>("/auth/kakao/status"),
  });

  // 이동이라서 mutation 이 아니다 — 성공하면 응답이 아니라 다른 페이지가 온다.
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  /**
   * 취소 복구 — 앱에서 인앱 브라우저의 X 를 누르고 돌아오면 이 화면이 그대로 살아 있어서
   * 버튼이 "로그인하는 중…" 에 멈춘다. 성공하면 화면 자체가 사라지므로 **되돌아온 경우만**
   * 풀어주면 된다. 🚨 취소는 실패가 아니다 — 배너를 띄우지 않고 버튼만 원상복구한다.
   */
  useEffect(() => {
    if (!pending) return;
    const onVisible = () => {
      if (document.visibilityState === "visible") setPending(false);
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => document.removeEventListener("visibilitychange", onVisible);
  }, [pending]);

  const notReady = status.data ? !status.data.ready : false;

  function start() {
    if (!status.data) return;
    setError(null);
    setPending(true);
    try {
      const started = startOAuthLogin("kakao", status.data);
      // 목에서는 카카오 왕복을 건너뛰고 콜백 화면으로 바로 간다 (§6).
      if (started.kind === "internal") router.replace(started.path);
    } catch (cause) {
      setPending(false);
      setError(
        cause instanceof OAuthUnavailableError || cause instanceof Error
          ? cause.message
          : "로그인을 시작하지 못했어요.",
      );
    }
  }

  return (
    <Screen className="justify-between gap-8">
      <div className="flex flex-col gap-6 pt-8">
        <div>
          {/* 🚨 canvas 위 브랜드 텍스트는 `brand` 다. `brand-ink` 는 soft 배경 위 전용 (문서 §2-2). */}
          <p className="text-label text-brand">육아기억</p>
          <PageTitle className="mt-2">
            아이 이야기를
            <br />
            여기에 모아둘게요
          </PageTitle>
          <p className="text-body text-ink-muted mt-3">
            육아를 가장 많이 아는 AI 가 아니라,
            <br />
            우리 아이를 가장 오래 알아온 AI.
          </p>
        </div>

        <Card>
          <ul className="text-body-sm text-ink-muted flex flex-col gap-3">
            {POINTS.map((point) => (
              <li key={point}>{point}</li>
            ))}
          </ul>
        </Card>
      </div>

      <div className="flex flex-col gap-3">
        {notReady ? (
          <CardFailed>로그인은 아직 연결 전이에요. 준비되면 이 버튼이 열려요.</CardFailed>
        ) : null}

        {error ? <CardFailed>{error}</CardFailed> : null}

        <Button
          variant="kakao"
          block
          onClick={start}
          disabled={pending || notReady || status.isPending}
        >
          <KakaoSymbol />
          {pending ? "로그인하는 중…" : "카카오로 시작하기"}
        </Button>

        <p className="text-caption text-ink-subtle text-center">
          이름(별명) · 나이 · 알레르기 여부까지만 물어봐요.
          <br />
          외부 서비스 연결은 요청하지 않아요.
        </p>
      </div>
    </Screen>
  );
}

/** 카카오 말풍선. 카카오 로그인 버튼은 카카오가 정한 모양을 써야 해서 lucide 를 쓰지 않는다. */
function KakaoSymbol() {
  return (
    <svg aria-hidden viewBox="0 0 18 18" width="18" height="18" fill="currentColor">
      <path d="M9 1.5C4.58 1.5 1 4.31 1 7.78c0 2.2 1.45 4.13 3.63 5.25-.16.57-.58 2.1-.66 2.43-.1.4.15.4.31.29.13-.09 2.03-1.38 2.85-1.94.6.09 1.22.13 1.87.13 4.42 0 8-2.81 8-6.28S13.42 1.5 9 1.5Z" />
    </svg>
  );
}
