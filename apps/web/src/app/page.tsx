"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardFailed } from "@/components/ui/card";
import { Screen } from "@/components/ui/screen";
import { api, qk, type AuthResponse, type Me } from "@/lib/api";
import { requestKakaoAccessToken } from "@/lib/auth/kakao";
import { useSessionStore } from "@/stores/session";

/**
 * 00 소개 · 로그인 — 프로토타입에 없는 화면이다.
 *
 * 소개는 일부러 짧게 뒀다. 여기서 서비스를 설명해서 설득하는 게 아니라,
 * "무엇을 모으고 무엇을 안 하는지" 만 먼저 말하고 로그인으로 보낸다. (자세한 소개는 다음 이슈)
 */

const POINTS = [
  "말하듯 한 줄만 남기면 AI 가 아이 기억으로 정리해요.",
  "식사 · 놀이 · 교육 · 건강, 지금 맞는 다음 행동을 준비해 둬요.",
  "쌓인 기억을 근거로 보여주고, 근거가 없으면 없다고 말해요.",
];

export default function LoginPage() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const signIn = useSessionStore((s) => s.signIn);

  /** 🚨 필수 동의가 남아 있으면 여기서 멈춘다 — 그 아래로 어떤 저장도 일어나면 안 된다 (계약서 §04). */
  const [consentRequired, setConsentRequired] = useState<string[] | null>(null);

  const login = useMutation({
    mutationFn: async () => {
      const accessToken = await requestKakaoAccessToken();
      return api.post<AuthResponse>("/auth/kakao", { access_token: accessToken });
    },
    onSuccess: async (auth) => {
      // 토큰은 먼저 넣는다 — 동의를 남기려면 인증된 요청을 보내야 한다.
      signIn(auth.token);

      if (auth.consent_required.length > 0) {
        setConsentRequired(auth.consent_required);
        return;
      }

      const me = await queryClient.fetchQuery({
        queryKey: qk.me(),
        queryFn: () => api.get<Me>("/me"),
      });
      const first = me.children[0];
      router.replace(first ? `/child/${first.child_id}/home` : "/onboarding");
    },
  });

  return (
    <Screen className="justify-between gap-8 py-8">
      <div className="flex flex-col gap-6 pt-8">
        <div>
          <p className="text-label text-brand-ink">육아기억</p>
          <h1 className="text-display text-ink mt-2">
            아이 이야기를
            <br />
            여기에 모아둘게요
          </h1>
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
        {consentRequired ? (
          <Card>
            <p className="text-body text-ink">먼저 동의가 필요해요</p>
            <p className="text-body-sm text-ink-muted mt-2">
              남은 동의 {consentRequired.length}건 — {consentRequired.join(" · ")}
            </p>
            <p className="text-caption text-ink-subtle mt-2">
              동의 화면은 아직 없어요 (10 설정). 그때까지 여기서 멈춥니다.
            </p>
          </Card>
        ) : null}

        {login.isError ? (
          <CardFailed>
            <p>{login.error instanceof Error ? login.error.message : "로그인하지 못했어요."}</p>
            <Button
              variant="tertiary"
              className="mt-1 -ml-2"
              onClick={() => login.mutate()}
              disabled={login.isPending}
            >
              다시 시도
            </Button>
          </CardFailed>
        ) : null}

        <Button
          variant="kakao"
          block
          onClick={() => {
            setConsentRequired(null);
            login.mutate();
          }}
          disabled={login.isPending}
        >
          <KakaoSymbol />
          {login.isPending ? "로그인하는 중…" : "카카오로 시작하기"}
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
