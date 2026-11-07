"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  CalendarCheck,
  MessageCircleQuestionMark,
  Mic,
  Shield,
  UsersRound,
  type LucideIcon,
} from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";

import { DomainChip, domainBar, domainField } from "@/components/domain-chip";
import { SessionChecking } from "@/components/session-checking";
import { SessionUnreachable } from "@/components/session-unreachable";
import { Button } from "@/components/ui/button";
import { Card, CardFailed } from "@/components/ui/card";
import { CountChip } from "@/components/ui/chip";
import { DOMAIN_ICON, DomainIcon } from "@/components/ui/icon";
import { IconTile } from "@/components/ui/icon-tile";
import { PageTitle } from "@/components/ui/page-title";
import { Screen } from "@/components/ui/screen";
import { Spinner } from "@/components/ui/spinner";
import { Swap } from "@/components/ui/swap";
import { PanelTabs } from "@/components/ui/tabs";
import { api, isApiError, qk, type Agent, type AuthStatus, type Me } from "@/lib/api";
import { landingPath } from "@/lib/auth/landing";
import { OAuthUnavailableError, startOAuthLogin } from "@/lib/auth/oauth";
import { cn } from "@/lib/cn";
import { PRIVACY_POLICY_HREF } from "@/lib/consent";
import { MEDICATION_ENABLED } from "@/lib/store-policy";
import { hasLiveSession, useSessionStore } from "@/stores/session";

/**
 * 00 소개 · 로그인 — 프로토타입에 없는 화면이다.
 *
 * 🚨 로그인 성공 처리는 여기 없다. 로그인은 페이지 이동이라 이 화면은 떠나고,
 *    돌아오는 곳은 `/auth/callback` 이다 (docs/web/kakao-login-v1.md §4-4).
 *
 * 로그인 버튼을 **첫 화면 안에 둔다.** 세션이 끊겨 다시 로그인하러 온 사람이 소개를 스크롤해
 * 내려가지 않게 한다. 소개는 그 아래에 길게 두고, 끝에 버튼을 한 번 더 세운다 — 다 읽은 사람이
 * 맨 위로 되돌아가지 않게. 바닥에 붙이지는 않는다 (디자인 시스템 "바닥에 붙이는 것").
 *
 * 🚨 **이 화면을 보는 사람은 대부분 처음 온 학부모다** — 세션이 살아 있으면 바로 홈으로 간다.
 *    그래서 기능 목록보다 **기록이 쌓이는 시간**을 보여준다 (#704). 가입 첫날 보게 될 "일반 추천" 을
 *    소개가 먼저 말해 두지 않으면, 첫 경험이 약속과 다르게 읽힌다.
 *
 * 🚨 **실서버에서 동작하는 것만 소개한다.** 사진으로 적기 · 검사지처럼 `lib/features.ts` 에
 *    걸린 기능은 넣지 않는다 — 소개를 보고 가입한 사람이 그 문을 찾지 못한다. 그 항목이 풀리면
 *    여기에 더한다. 문구의 근거는 고객센터 답(`lib/help.ts`)과 같은 규칙이다.
 *
 * 🚨 **예시는 전부 지어낸 것이다.** 실제 사용자 발화나 아이 정보를 넣지 않는다 (최상위 CLAUDE.md §9).
 *    예시 카드는 서버가 준 근거처럼 읽히지 않게 "예시" 를 명시한다.
 */

/**
 * 예시 속 기록 세 줄. 위 예시 카드의 근거 목록과 아래 타임라인이 **같은 세 줄**이다 —
 * "무엇이 나오나" 와 "어떻게 쌓였나" 가 한 이야기로 이어진다.
 * 요일은 표시용 글자다 (날짜를 계산하지 않는다).
 */
const EXAMPLE_RECORDS = [
  { day: "월요일", text: "어린이집에서 블록만 한참 쌓았대요" },
  { day: "수요일", text: "집에 와서도 블록으로 탑을 쌓았어요" },
  { day: "금요일", text: "종이컵을 높이 쌓아 보고 좋아했어요" },
] as const;

const EXAMPLE_QUESTION = "주말에 뭐 하고 놀까요?";

/** 한 줄이 여러 기록으로 나뉘는 예시. 🚨 도메인 4색은 이 화면에서만 다 쓴다 (디자인 시스템 §2-3 예외 ㉠). */
const SPLIT_INPUT = "점심은 거의 다 먹었고, 낮잠 자고 나서 기침을 조금 했대요";
const SPLIT_RECORDS: Array<{ agent: Agent; text: string }> = [
  { agent: "food", text: "점심을 거의 다 먹었어요" },
  { agent: "health", text: "낮잠 뒤에 기침을 조금 했어요" },
];

const DOMAINS: Array<{ agent: Agent; label: string; text: string }> = [
  {
    agent: "food",
    label: "식사",
    // 🚨 "오늘 급식" 을 말하지 않는다 — 급식은 사진으로 적기(08)로 들어오는데 실서버에 아직 없다.
    text: "등록한 알레르기를 먼저 걸러 낸 뒤 끼니 메뉴를 골라요. 알레르기를 확인하지 못하면 식사 제안을 만들지 않아요.",
  },
  {
    agent: "activity",
    label: "놀이",
    text: "요즘 빠져 있는 것에서 다음 놀이를 이어 가요. 위치를 허용하면 가까운 공원이나 도서관도 찾아요.",
  },
  {
    agent: "growth",
    label: "성장",
    text: "관심이 향한 쪽으로 함께 읽을 책과 해 볼 만한 것을 골라요.",
  },
  {
    agent: "health",
    label: "건강",
    // 복약은 #713 에서 실서버에 붙었다. 🚨 "알려 드려요" 를 말하지 않는다 — 복약 알림 발송은 아직 없다.
    // 🚨 스토어 선언 때문에 꺼 둔 동안은 복약을 말하지 않는다 (`lib/store-policy.ts`).
    text: MEDICATION_ENABLED
      ? "증상 기록을 모아 정리해요. 먹일 약과 시간을 적으면 복약 일정을 준비해 두고, 확인한 뒤에 넣어요. 같은 증상이 이어지면 병원 확인을 권하고, 진단은 하지 않아요."
      : "증상 기록을 모아 정리해요. 같은 증상이 이어지면 병원 확인을 권하고, 진단은 하지 않아요.",
  },
];

/**
 * 승인 게이트 2곳 (최상위 CLAUDE.md §2). 🚨 **여기는 게이트가 아니라 게이트의 설명이다** —
 * `caution` · `btn-approve` 를 쓰지 않는다. 타일은 브랜드 톤이다 (아이콘 타일은 브랜드가 서는 자리다).
 */
const GATES: Array<{ icon: LucideIcon; title: string; text: string }> = [
  {
    icon: CalendarCheck,
    title: "캘린더에 일정 넣기",
    text: "고른 제안으로 일정 초안까지만 만들어요. 캘린더에는 보호자가 확인하고 넣어야 들어가요.",
  },
  {
    icon: Shield,
    title: "알레르기와 건강 기록",
    text: "AI가 짐작해서 적지 않아요. 보호자가 적은 것만, 확인한 뒤에 저장해요.",
  },
];

/** CLAUDE.md §1 "안 만드는 것" 에서. 무엇을 안 하는지가 이 서비스의 절반이다. */
const NOT_DOING = [
  "진단하거나 약을 권하지 않아요. 반복되는 증상은 병원에 가시라고 말해요.",
  "상품을 추천하거나 광고를 넣지 않아요.",
  // 🚨 처리방침 1장과 같은 범위여야 한다 (최상위 CLAUDE.md §2 개인정보 — 별명 · 생일 · 알레르기와
  //    건강 정보 + 선택 항목 성별 · 키 · 몸무게). 받는 항목이 늘면 이 줄과 처리방침을 같이 고친다.
  //    "아이에 대해서는" 을 떼지 않는다 — 보호자의 부르는 이름 · 아이와의 관계도 묻는다(처리방침 한눈에 보기).
  "아이에 대해서는 별명, 생일, 알레르기와 건강 정보 말고는 묻지 않아요. 성별, 키, 몸무게는 원할 때만 적어요.",
];

/**
 * 🚨 **앱은 늘 이 주소로 열린다** (`apps/mobile` 의 `WEB_URL`). 그래서 로그인 화면을 그리기 전에
 *    세션부터 확인한다 — 살아 있으면 홈으로 보낸다. 안 그러면 refresh 쿠키로 세션을 되살려
 *    놓고도 앱을 켤 때마다 로그인 화면이 선다 (docs/web/auth-consent-v1.md §2).
 */
export default function LandingPage() {
  const hydrated = useSessionStore((s) => s.hydrated);
  const live = useSessionStore(hasLiveSession);
  const restoreFailed = useSessionStore((s) => s.restoreFailed);

  // 확인하는 동안 로그인 화면을 그리지 않는다 — 깜빡였다가 홈으로 넘어간다.
  if (!hydrated) return <SessionChecking />;
  if (live) return <ResumeSession />;
  if (restoreFailed) return <SessionUnreachable />;
  return <LoginScreen />;
}

/** 세션이 살아 있다. 마지막에 본 아이의 홈으로 보낸다. `/me` 를 받는 동안도 같은 확인 중 표시다. */
function ResumeSession() {
  const router = useRouter();
  const queryClient = useQueryClient();

  useEffect(() => {
    let cancelled = false;
    void queryClient
      .fetchQuery({ queryKey: qk.me(), queryFn: () => api.get<Me>("/me") })
      .then((me) => landingPath(me, useSessionStore.getState().activeChildId))
      // 🚨 `/me` 실패는 로그아웃이 아니다 — 아이가 있는지 모를 뿐이다. 00-1 은 그 실패를 이미
      //    감당한다 (콜백 화면과 같은 판단 · `app/auth/callback/page.tsx`). 세션이 끝난 401 이면
      //    전역 처리가 먼저 이 화면으로 되돌린다.
      .catch((cause: unknown) => (isApiError(cause, "unauthenticated") ? null : "/start"))
      .then((path) => {
        if (path && !cancelled) router.replace(path);
      });
    return () => {
      cancelled = true;
    };
  }, [queryClient, router]);

  return <SessionChecking />;
}

function LoginScreen() {
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

  /**
   * 🚨 **두 버튼은 같은 상태를 나눠 쓴다.** 한쪽을 누르면 둘 다 "로그인하는 중…" 이 된다 —
   *    한쪽만 돌면 아래 버튼을 한 번 더 눌러 로그인이 두 번 시작될 수 있다.
   *    안내(준비 전 · 실패)는 위 버튼 곁에만 둔다. 같은 말이 화면에 두 번 서지 않게.
   */
  const kakao = (
    <KakaoButton
      pending={pending}
      disabled={pending || notReady || status.isPending}
      onStart={start}
    />
  );

  return (
    // 🚨 `waiting` — 로그인 준비 여부를 받는 동안은 앱 셸의 스플래시를 걷지 않는다 (#690). 그동안 버튼이 꺼져
    //    회색인데, 걷히는 순간 그 회색이 한 번 보였다가 카카오 노랑으로 바뀌었다 (에뮬레이터 확인).
    //    실패해도 `isPending` 은 끝나므로 걷힌다 — 셸의 시간 초과까지 남지 않는다.
    <Screen className="gap-10" waiting={status.isPending}>
      <div className="flex flex-col gap-6">
        <header>
          <p className="text-label text-brand">아이캐치</p>
          {/* 새 습관을 요구하지 않고 수신처만 바꾼다 (최상위 CLAUDE.md §1 핵심 통찰). */}
          <PageTitle className="mt-2">가족에게 하던 아이 이야기, 여기에도 들려주세요</PageTitle>
          <p className="text-body text-ink-muted mt-3">
            따로 정리하지 않아도 이야기가 기록으로 쌓이고, 쌓인 기록을 근거로 오늘 해 볼 만한 것을
            골라 드려요.
          </p>
        </header>

        {/* 🚨 버튼이 예시보다 위다. 예시 카드는 탭마다 키가 달라서 그 아래 두면 360×740 폰에서
            첫 화면 밖으로 밀렸다 (#704). 예시는 처음 온 사람이 버튼 아래에서 읽는다. */}
        <div className="flex flex-col gap-3">
          {notReady ? (
            <CardFailed>로그인은 아직 연결 전이에요. 준비되면 이 버튼이 열려요.</CardFailed>
          ) : null}
          {error ? <CardFailed>{error}</CardFailed> : null}
          {kakao}
          <p className="text-caption text-ink-subtle text-center">
            가입하면 약관과 개인정보 처리에 동의하게 돼요.
            <br />
            외부 서비스 연결은 요청하지 않아요.
          </p>
        </div>
      </div>

      <IntroSection
        title="기록이 쌓일수록 제안이 달라져요"
        description="같은 질문에도 처음 며칠과 기록이 쌓인 뒤의 답이 달라요."
      >
        <ExamplePreview />
      </IntroSection>

      <IntroSection
        title="한 줄이 기억이 되기까지"
        description="한 번 나온 이야기로 아이의 성향을 정하지 않아요. 같은 이야기가 몇 번 나왔는지는 AI가 아니라 정해진 규칙으로 세어요."
      >
        <RecordTimeline />
        <p className="text-body-sm text-ink-muted">
          틀렸으면 기록과 기억 화면에서 &lsquo;이번만 그랬어요&rsquo;, &lsquo;지금은
          달라요&rsquo;처럼 골라서 바로 고칠 수 있어요.
        </p>
      </IntroSection>

      <IntroSection
        title="말하듯 남기면 돼요"
        description="정해진 형식이 없어요. 한 줄에 여러 이야기가 섞여 있어도 나눠서 남겨요."
      >
        <SplitPreview />
        <ul className="flex flex-col gap-3">
          <li>
            <IconRow
              icon={Mic}
              title="앱에서는 말로 적기"
              text="지원하는 휴대폰이면 마이크로 말해서 적을 수 있어요. 목소리는 휴대폰 안에서 글자로 바뀌고, 녹음은 보내지 않아요."
            />
          </li>
          <li>
            <IconRow
              icon={MessageCircleQuestionMark}
              title="기록하면서 물어보기"
              text="기록만 적으면 저장만 하고, '저녁 뭐 먹이지?'처럼 물어보면 그때 제안을 준비해요."
            />
          </li>
        </ul>
      </IntroSection>

      <IntroSection
        title="쌓인 기록으로 네 가지를 준비해요"
        description="한 번에 두 가지까지만 꺼내요. 매번 네 개를 다 보여주지 않아요."
      >
        <ul className="flex flex-col gap-4">
          {DOMAINS.map((domain) => (
            <li key={domain.agent}>
              <DomainRow {...domain} />
            </li>
          ))}
        </ul>
      </IntroSection>

      <IntroSection
        title="직접 확인하는 건 두 가지뿐이에요"
        description="되돌릴 수 없는 것만 보호자 앞으로 가져와요. 나머지는 따로 확인받지 않고 정리해요."
      >
        <ul className="flex flex-col gap-4">
          {GATES.map((gate) => (
            <li key={gate.title}>
              <IconRow {...gate} />
            </li>
          ))}
        </ul>
      </IntroSection>

      <IntroSection title="가족과 같이 봐요">
        <IconRow
          icon={UsersRound}
          title="초대 코드로 연결"
          text="코드를 보내면 배우자, 조부모, 돌봄 선생님도 같은 아이의 기록과 제안을 함께 봐요. 누가 무엇을 적었는지는 기록마다 남아요."
        />
      </IntroSection>

      <IntroSection title="이런 건 하지 않아요">
        {/* 마커는 CSS 로 그린다 — 화면에 기호를 글자로 찍지 않는다. */}
        <ul className="text-body-sm text-ink-muted marker:text-ink-subtle flex list-disc flex-col gap-2 pl-5">
          {NOT_DOING.map((line) => (
            <li key={line}>{line}</li>
          ))}
        </ul>
        <p className="text-caption text-ink-subtle">
          받는 항목과 보관 기간은{" "}
          <a
            href={PRIVACY_POLICY_HREF}
            target="_blank"
            rel="noopener noreferrer"
            className="text-ink-muted decoration-line-strong -my-3 inline-block py-3 underline decoration-1 underline-offset-4"
          >
            개인정보 처리방침
          </a>
          에 있어요.
        </p>
      </IntroSection>

      {/* 끝맺음 — 포지셔닝 한 문장 + 버튼 한 번 더. 위의 안내(준비 전 · 실패)는 되풀이하지 않는다. */}
      <section className="border-line flex flex-col gap-4 border-t pt-8 pb-2">
        <p className="text-title text-ink">
          육아를 가장 많이 아는 AI가 아니라, 우리 아이를 가장 오래 알아온 AI.
        </p>
        {kakao}
      </section>
    </Screen>
  );
}

function KakaoButton({
  pending,
  disabled,
  onStart,
}: {
  pending: boolean;
  disabled: boolean;
  onStart: () => void;
}) {
  return (
    <Button variant="kakao" block onClick={onStart} disabled={disabled}>
      {pending ? <Spinner /> : <KakaoSymbol />}
      {pending ? "로그인하는 중…" : "카카오로 시작하기"}
    </Button>
  );
}

/**
 * 소개의 한 구역. 🚨 구역을 카드로 두르지 않는다 — 상자는 안에 든 예시가 가진다 (`ui/section.tsx` 머리말).
 * 제목 위에 따로 라벨을 얹지 않는다. 제목이 제 무게를 진다.
 */
function IntroSection({
  title,
  description,
  children,
}: {
  title: string;
  description?: string;
  children: ReactNode;
}) {
  return (
    <section className="flex flex-col gap-4">
      <div>
        <h2 className="text-section text-ink">{title}</h2>
        {description ? <p className="text-body-sm text-ink-muted mt-1">{description}</p> : null}
      </div>
      {children}
    </section>
  );
}

/**
 * 제품이 실제로 무엇을 내놓는지 — **처음 며칠 / 기록이 쌓이면** 두 장.
 *
 * 🚨 **실제 추천이 아니라 예시다.** "예시" 를 명시해서 서버가 준 근거처럼 읽히지 않게 한다.
 * 🚨 **두 장은 05 의 두 컴포넌트를 본뜬다** — 일반 추천은 `card-general`(점선 · `canvas` ·
 *    "일반 추천" 라벨 · 기록 건수), 개인화는 `suggestion-row`(도메인 띠 · 추천 이유 · 사용한 기록 목록).
 *    뜻을 나르는 모양만 가져왔다 — 일반 추천의 "참고 · 출처" 줄, 고르는 칸은 없다.
 *    가입한 뒤 05 에서 만날 모양과 같아야 소개가 약속한 것을 알아본다. 둘을 가르는 것은 **라벨과 건수**지
 *    색이 아니다 (디자인 시스템 §2-3). 고르는 칸(체크박스)은 그리지 않는다 — 눌러도 아무 일이 없다.
 * ⚠️ 두 장의 키가 달라서 탭을 바꾸면 아래가 움직인다. 한때 grid 한 칸에 겹쳐 키를 맞췄는데, 짧은 장에서
 *    카드 안 아래 절반이 비었다. 누른 순간에만 움직이고, 그 아래에 로그인 버튼이 없어서 감수한다.
 * 기본은 "기록이 쌓이면" 이다 — 처음 온 사람에게 먼저 보여줄 것은 이 서비스가 닿는 곳이다.
 */
type ExampleKey = "first" | "later";

const EXAMPLE_TABS = [
  { key: "first", label: "처음 며칠" },
  { key: "later", label: "기록이 쌓이면" },
] as const satisfies ReadonlyArray<{ key: ExampleKey; label: string }>;

const EXAMPLE_PANEL_ID = "landing-example";

function ExamplePreview() {
  const [tab, setTab] = useState<ExampleKey>("later");

  return (
    <Card className="flex flex-col gap-4 pt-2">
      <PanelTabs
        items={EXAMPLE_TABS}
        active={tab}
        onChange={(key) => setTab(key as ExampleKey)}
        label="예시 고르기"
        panelId={EXAMPLE_PANEL_ID}
      />

      <div
        id={EXAMPLE_PANEL_ID}
        role="tabpanel"
        aria-labelledby={`${EXAMPLE_PANEL_ID}-tab-${tab}`}
        className="flex flex-col gap-3"
      >
        <div className="flex items-center justify-between gap-3">
          <p className="text-caption text-ink-subtle">예시</p>
          {/* 🚨 `brand` 로 칠하지 않는다. 04 의 초록 말풍선은 브랜드색이 서는 여섯째 자리이고 "다른 말풍선으로
              넓히지 않는다" (디자인 시스템 §2-2). 여기는 그 화면이 아니라 예시라 뉴트럴 면에 둔다. */}
          <p className="bg-surface-muted rounded-card text-body-sm text-ink max-w-[85%] px-4 py-2.5">
            {EXAMPLE_QUESTION}
          </p>
        </div>

        {/* 같은 자리의 내용이 바뀐다 — 200ms 페이드 (디자인 시스템 §8 "교체"). */}
        <Swap id={tab}>{tab === "first" ? <GeneralExample /> : <PersonalizedExample />}</Swap>
      </div>
    </Card>
  );
}

function GeneralExample() {
  return (
    <>
      <div className="rounded-card bg-canvas border-line-strong border border-dashed p-4">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1.5">
          <DomainChip agent="activity" />
          <span className="text-label text-ink-muted">일반 추천</span>
          <CountChip>기록 0건</CountChip>
        </div>
        <p className="text-body text-ink mt-3">
          신문지를 뭉쳐 공을 만들고 바구니에 던져 넣어 보세요.
        </p>
        <p className="text-body-sm text-ink-muted mt-1">
          아직 놀이 기록이 없어서 또래 아이들이 많이 하는 놀이로 골랐어요.
        </p>
      </div>
      <p className="text-body-sm text-ink-muted">
        기록이 없을 때는 우리 아이 맞춤인 척하지 않아요. 일반 추천이라고 적고, 쌓인 기록 수를 그대로
        보여 드려요.
      </p>
    </>
  );
}

function PersonalizedExample() {
  return (
    <>
      <div className="rounded-card bg-surface border-line flex overflow-hidden border">
        <span aria-hidden className={cn("w-1.5 shrink-0", domainBar("activity"))} />
        <div className="min-w-0 flex-1 p-4">
          <p className="text-body text-ink">종이컵으로 탑을 쌓고 누가 더 높이 쌓나 겨뤄 보세요.</p>
          <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-3">
            <dt className="text-caption text-ink-subtle">추천 이유</dt>
            <dd className="text-body-sm text-ink-muted">
              요즘 쌓는 놀이 이야기가 이어지고 있어요.
            </dd>
          </dl>
          <div className="border-line mt-3 border-t pt-3">
            <p className="text-label text-ink-muted">사용한 기록 {EXAMPLE_RECORDS.length}건</p>
            <ul className="mt-2 flex flex-col gap-2.5">
              {EXAMPLE_RECORDS.map((record) => (
                <li key={record.day}>
                  <p className="text-body-sm text-ink">{record.text}</p>
                  <p className="text-caption text-ink-subtle mt-0.5">{record.day} 기록</p>
                </li>
              ))}
            </ul>
          </div>
        </div>
      </div>
      <p className="text-body-sm text-ink-muted">
        기록이 근거가 되면 어떤 기록을 썼는지 하나하나 붙여서 보여 드려요.
      </p>
    </>
  );
}

/**
 * 기록 세 줄 → 기억 하나. 🚨 점은 09 캘린더의 "찬 점 = 기록" 과 같은 모양(찬 원)이다 — 가입한 뒤 달력에서
 * 같은 점을 만나면 이미 아는 뜻이 된다. 크기는 이 줄 글자에 맞춰 키웠다(달력 칸의 점보다 크다).
 * 색은 그 기록의 도메인(놀이)이고, 뜻은 옆의 글자가 진다.
 *
 * 끝의 카드는 07 기억 탭의 **후보** 카드를 본뜬다 (`affinity-list.tsx` — 점선 `line-strong` · `canvas` ·
 * "후보" 칩). 🚨 "확인됨" 으로 그리지 않는다 — 기록 세 줄로 확정된 것처럼 보이면 이 구역이 말하는
 * "한 번으로 정하지 않는다" 의 반대말이 된다. 몇 번이면 확인되는지(서버 설정값)도 적지 않는다.
 */
function RecordTimeline() {
  return (
    <div className="flex flex-col gap-4">
      <ol className="relative flex flex-col gap-4 pl-6">
        {/* 점들을 잇는 세로선. 첫 점 중심에서 마지막 점 중심까지만. */}
        <span aria-hidden className="bg-line absolute top-3 bottom-3 left-[4px] w-px" />
        {EXAMPLE_RECORDS.map((record) => (
          <li key={record.day} className="relative">
            <span
              aria-hidden
              className="text-activity absolute top-[0.4rem] -left-6 size-[0.5625rem] rounded-full bg-current"
            />
            <p className="text-caption text-ink-subtle">{record.day}</p>
            <p className="text-body-sm text-ink mt-0.5">{record.text}</p>
          </li>
        ))}
      </ol>

      <div className="rounded-card bg-canvas border-line-strong flex items-start gap-3 border border-dashed p-4">
        <IconTile icon={DOMAIN_ICON.activity} tone="plain" className={domainField("activity")} />
        <div className="flex min-w-0 flex-1 flex-col gap-1.5">
          <p className="text-title text-ink">쌓기 놀이</p>
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="text-caption bg-surface-muted text-ink-muted min-h-chip inline-flex items-center rounded-full px-2.5 py-0.5">
              후보
            </span>
            <span className="text-caption text-ink-subtle">
              놀이 · 기록 {EXAMPLE_RECORDS.length}건
            </span>
          </div>
          <p className="text-body-sm text-ink-muted">
            같은 이야기가 더 이어지면 확인된 기억이 돼요.
          </p>
        </div>
      </div>
    </div>
  );
}

/** 한 줄 → 기록 N건. 04 대화에서 저장한 기록으로 돌려받는 것을 미리 보여준다 (모양은 소개에 맞춘 것이다). */
function SplitPreview() {
  return (
    <Card className="flex flex-col gap-3">
      <p className="text-caption text-ink-subtle">예시</p>
      <p className="border-line rounded-field bg-canvas text-body-sm text-ink border px-3 py-2">
        {SPLIT_INPUT}
      </p>
      <p className="text-caption text-ink-subtle">기록 {SPLIT_RECORDS.length}건으로 나눠 남겨요</p>
      <ul className="flex flex-col gap-2.5">
        {SPLIT_RECORDS.map((record) => (
          <li key={record.agent} className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <DomainChip agent={record.agent} />
            <span className="text-body-sm text-ink">{record.text}</span>
          </li>
        ))}
      </ul>
    </Card>
  );
}

/** 아이콘 타일 + 제목 + 설명 한 줄. 🚨 아이콘은 `aria-hidden` 이라 뜻은 제목이 진다. */
function IconRow({ icon, title, text }: { icon: LucideIcon; title: string; text: string }) {
  return (
    <div className="flex items-start gap-3">
      <IconTile icon={icon} />
      <div className="flex min-w-0 flex-col gap-0.5">
        <p className="text-body text-ink">{title}</p>
        <p className="text-body-sm text-ink-muted">{text}</p>
      </div>
    </div>
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
    growth: "text-growth-ink",
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
