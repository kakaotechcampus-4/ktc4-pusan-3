"use client";

import { useQuery } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardFailed } from "@/components/ui/card";
import { DomainIcon } from "@/components/ui/icon";
import { PageTitle } from "@/components/ui/page-title";
import { Screen } from "@/components/ui/screen";
import { Spinner } from "@/components/ui/spinner";
import { api, qk, type Agent, type AuthStatus } from "@/lib/api";
import { OAuthUnavailableError, startOAuthLogin } from "@/lib/auth/oauth";

/**
 * 00 소개 · 로그인 — 프로토타입에 없는 화면이다.
 *
 * 🚨 로그인 성공 처리는 여기 없다. 로그인은 페이지 이동이라 이 화면은 떠나고,
 *    돌아오는 곳은 `/auth/callback` 이다 (docs/web/kakao-login-v1.md §4-4).
 *
 * 로그인 버튼을 **소개 위쪽에 둔다.** 이 화면에 오는 사람 대부분은 로그인하러 온 것이고,
 * 버튼을 읽고 스크롤해야 나오게 만들면 매일 여는 앱에서 그 스크롤이 계속 쌓인다.
 * 소개는 처음 온 사람을 위해 그 아래에 둔다.
 */

/** 🚨 지어낸 예시다. 실제 사용자 발화나 아이 정보를 넣지 않는다 (최상위 CLAUDE.md §9). */
const EXAMPLE_INPUT = "오늘 어린이집에서 블록만 한참 쌓았대요";

const DOMAINS: Array<{ agent: Agent; label: string; text: string }> = [
  { agent: "food", label: "식사", text: "오늘 급식과 알레르기를 함께 보고 저녁 한 끼를 고릅니다." },
  { agent: "activity", label: "놀이", text: "요즘 빠져 있는 것에서 다음 놀이를 이어 붙입니다." },
  { agent: "education", label: "교육", text: "관심이 향한 방향으로 다음 한 걸음을 제안합니다." },
  { agent: "health", label: "건강", text: "증상과 기록을 정리해 둡니다. 진단은 하지 않습니다." },
];

/** CLAUDE.md §1 "안 만드는 것" 에서. 무엇을 안 하는지가 이 서비스의 절반이다. */
const NOT_DOING = [
  "진단하거나 약을 권하지 않아요. 반복되는 증상은 병원에 가시라고 말해요.",
  "상품을 추천하거나 광고를 넣지 않아요.",
  "이름(별명), 나이, 알레르기 여부까지만 물어봐요.",
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
    <Screen className="gap-8">
      <header>
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
      </header>

      <ExamplePreview />

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
          {pending ? <Spinner /> : <KakaoSymbol />}
          {pending ? "로그인하는 중…" : "카카오로 시작하기"}
        </Button>
        <p className="text-caption text-ink-subtle text-center">
          가입하면 약관과 개인정보 처리에 동의하게 돼요.
          <br />
          외부 서비스 연결은 요청하지 않아요.
        </p>
      </div>

      <section className="border-line flex flex-col gap-4 border-t pt-8">
        <div>
          <h2 className="text-section text-ink">쌓인 기억으로 네 가지를 준비해요</h2>
          <p className="text-body-sm text-ink-muted mt-1">
            한 번에 두 가지까지만 꺼내요. 매번 네 개를 다 보여주지 않아요.
          </p>
        </div>
        <ul className="flex flex-col gap-3">
          {DOMAINS.map((domain) => (
            <li key={domain.agent}>
              <DomainRow {...domain} />
            </li>
          ))}
        </ul>
      </section>

      <section className="flex flex-col gap-3 pb-2">
        <h2 className="text-section text-ink">이런 건 하지 않아요</h2>
        {/* 마커는 CSS 로 그린다 — 화면에 기호를 글자로 찍지 않는다. */}
        <ul className="text-body-sm text-ink-muted marker:text-ink-subtle flex list-disc flex-col gap-2 pl-5">
          {NOT_DOING.map((line) => (
            <li key={line}>{line}</li>
          ))}
        </ul>
      </section>
    </Screen>
  );
}

/**
 * 제품이 실제로 무엇을 내놓는지 한 장으로 보여준다.
 *
 * 🚨 실제 추천이 아니라 **예시**다. 화면에 "예시" 를 명시해서 서버가 준 근거처럼 읽히지 않게 한다.
 *    근거 칩을 일부러 함께 그린다 — 이 서비스는 근거 없이 "우리 아이 맞춤" 인 척하지 않는다.
 */
function ExamplePreview() {
  return (
    <Card className="flex flex-col gap-3">
      <p className="text-caption text-ink-subtle">예시</p>

      <p className="border-line rounded-field bg-canvas text-body-sm text-ink border px-3 py-2">
        {EXAMPLE_INPUT}
      </p>

      <p className="text-caption text-ink-subtle">이렇게 정리해서 내놔요</p>

      <div className="bg-brand-soft rounded-card p-4">
        <span className="bg-surface text-activity-ink text-label inline-flex h-7 items-center gap-1.5 rounded-full px-2.5">
          <DomainIcon agent="activity" />
          놀이
        </span>
        <p className="text-body text-brand-ink mt-2">
          쌓기가 계속되고 있어요. 종이컵 탑을 같이 세워 보는 건 어때요?
        </p>
        <p className="text-caption text-ink-muted mt-2">사용한 기록 3건 · 가장 최근 오늘</p>
      </div>
    </Card>
  );
}

/**
 * 도메인 4종. 🚨 아이콘은 aria-hidden 이라 의미는 옆의 라벨이 진다 (디자인 시스템 §3).
 *
 * 아이콘을 파스텔 원 안에 넣지 않는다. 그 원은 뜻을 하나도 나르지 않는데 —
 * 도메인은 옆의 라벨이 이미 말하고 있다 — 화면에서 제일 눈에 띄는 물건이 됐다.
 * 디자인 시스템 §1 이 "예쁘라고 칠하는 색은 없다" 고 정한 자리다. 색은 아이콘이 그대로 낸다.
 */
function DomainRow({ agent, label, text }: { agent: Agent; label: string; text: string }) {
  const tone: Record<Agent, string> = {
    food: "text-food-ink",
    activity: "text-activity-ink",
    education: "text-education-ink",
    health: "text-health-ink",
  };

  return (
    <div className="flex items-start gap-3">
      {/* 아이콘 20px 을 본문 첫 줄(16px · 1.6 = 25.6px)의 광학 중심에 맞춘다. */}
      <span className={`flex h-6 w-5 shrink-0 items-center justify-center ${tone[agent]}`}>
        <DomainIcon agent={agent} size="md" />
      </span>
      <span className="flex flex-col gap-0.5">
        <span className="text-body text-ink">{label}</span>
        <span className="text-body-sm text-ink-muted">{text}</span>
      </span>
    </div>
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
