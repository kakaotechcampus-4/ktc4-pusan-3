"use client";

import { useQuery } from "@tanstack/react-query";
import {
  CalendarDays,
  MessagesSquare,
  NotebookPen,
  Repeat,
  Settings,
  Utensils,
  type LucideIcon,
} from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";

import { AuthGate } from "@/components/auth-gate";
import { ChildNav } from "@/components/child-nav";
import { ConsentRequiredCard } from "@/components/consent-required-card";
import { HomeComposer } from "@/components/home-composer";
import { PhotoSourceSheet } from "@/components/photo-source-sheet";
import { PROMPT_PILL } from "@/components/agent-prompts";
import { Button } from "@/components/ui/button";
import { Card, CardFailed } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { IconButton } from "@/components/ui/icon-button";
import { ICON_SIZE, ICON_STROKE } from "@/components/ui/icon";
import { IconTile } from "@/components/ui/icon-tile";
import { PageTitle } from "@/components/ui/page-title";
import { Screen } from "@/components/ui/screen";
import { SkeletonBlock } from "@/components/ui/skeleton";
import { useChildId } from "@/hooks/use-child-id";
import { hasOpenQuestion, isBusy, sendLine, useConversation } from "@/stores/conversation";
import { useDraftStore, useDraftText } from "@/stores/draft";
import { usePhotoDraftStore } from "@/stores/photo-draft";
import { api, isApiError, qk, type Agent, type HomeResponse, type Me } from "@/lib/api";
import { cn } from "@/lib/cn";

/**
 * 03 홈 · 한 줄 입력.
 *
 * 🚨 **보내는 순간 04 대화 화면으로 넘어간다** (#226). 202 를 기다리지 않는다 — 말풍선이 먼저 서고
 *    요청은 뒤에서 나간다 (`stores/conversation.ts`). 그래서 **이 화면은 실패를 그리지 않는다.**
 *    보내기 실패 · 429 · 400 은 대화 화면의 그 말풍선 아래 한 곳에 선다.
 *
 * 🚨 **아직 안 보낸 원문은 이 화면이 들고 있지 않다** (`stores/draft.ts`). 하단 네비가
 *    보내기 버튼 바로 아래에 다른 화면으로 가는 문을 세 개 열어서, 화면 로컬 상태로 두면
 *    잘못 누른 한 번에 쓰던 글이 사라진다. 저장소가 아니라 **메모리**에 둔다 —
 *    발화 원문은 디스크에 남기지 않는다 (최상위 CLAUDE.md §2).
 *
 * 🚨 **빈 상태는 사과문이 아니다.** `highlight: null` 이면 쌓인 기록 건수를 그대로 보여준다
 *    (CLAUDE.md §2 — 근거가 없으면 없다고 말한다).
 */
export default function HomePage() {
  return (
    <AuthGate>
      <HomeScreen />
    </AuthGate>
  );
}

function HomeScreen() {
  const childId = useChildId();
  const router = useRouter();

  // 🚨 `useState` 가 아니다 — 네비로 다녀와도 쓰던 글이 살아 있어야 한다 (위 주석 · stores/draft.ts).
  const [text, setText] = useDraftText(childId);
  const clearDraft = useDraftStore((s) => s.clearDraft);
  /**
   * 사진은 **여기서 고르고 08 에서 확인한다.** 고르는 것은 두 갈래 한 번이라 화면을 따로
   * 두지 않고(`PhotoSourceSheet`), 고른 파일은 라우트를 못 넘어가서 스토어로 넘긴다
   * (`stores/photo-draft.ts` — 메모리 전용).
   */
  const [photoSheetOpen, setPhotoSheetOpen] = useState(false);
  const putPhoto = usePhotoDraftStore((s) => s.putPhoto);

  const conversation = useConversation(childId);
  const busy = isBusy(conversation);
  /**
   * 막 보낸 한 줄. 화면이 넘어가는 동안 입력창에 **그대로 둔다** — 이 글자가 대화 화면의 첫 말풍선
   * 자리로 이어 붙는다 (`VIEW_TRANSITION.sentLine`). 🚨 draft 스토어는 이미 비웠다 — 이 값은
   * 넘어가는 한 프레임의 그림일 뿐이고, 원문의 정본은 말풍선이다.
   */
  const [leaving, setLeaving] = useState<string | null>(null);
  const chatHref = `/child/${childId}/chat`;

  // 보내는 순간 넘어가야 해서 미리 받아 둔다. 안 받아 두면 첫 이동이 그 주소를 받는 동안 멈춘다.
  useEffect(() => {
    router.prefetch(chatHref);
  }, [router, chatHref]);

  const home = useQuery({
    queryKey: qk.home(childId),
    queryFn: () => api.get<HomeResponse>(`/children/${childId}/home`),
  });

  // 🚨 이름은 스토어에 캐시하지 않는다. 개인정보는 화면이 필요할 때 Query 로 가져온다 (§3).
  const me = useQuery({ queryKey: qk.me(), queryFn: () => api.get<Me>("/me") });
  const nickname = me.data?.children.find((c) => c.child_id === childId)?.nickname;

  /**
   * 🚨 **03 에서 보낸 한 줄은 질문에 대한 답이 아니다** (`asReply: false`). 답을 기다리는 질문이
   *    있어도 여기서는 그 질문이 화면에 없다 — 보이지 않는 질문의 답으로 묶지 않는다. 답하는 자리는
   *    대화 화면이고, 입구가 "답을 기다리는 질문이 있어요" 로 그리로 부른다.
   */
  function send() {
    const line = text;
    if (!sendLine(childId, line, { asReply: false, source: "home_input" })) return;
    // 셋이 한 번에 그려진다 — 입력창은 막 보낸 글자를 든 채로 대화 화면으로 넘어간다.
    setLeaving(line);
    clearDraft(childId);
    router.push(chatHref);
  }

  const consentBlocked = isApiError(home.error, "consent_required") ? home.error : null;

  function goToSuggestions(agents: Agent[]) {
    const params = new URLSearchParams({ agents: agents.join(",") });
    router.push(`/child/${childId}/suggestions?${params.toString()}`);
  }

  return (
    <Screen
      className="gap-5"
      nav={<ChildNav active="home" />}
      bottomBar={
        <div className="flex flex-col gap-2">
          <HomeComposer
            value={leaving ?? text}
            onChange={setText}
            onSubmit={send}
            prompts={home.data?.agent_prompts ?? []}
            onPickPrompt={(agent) => goToSuggestions([agent])}
            onPickPhoto={() => setPhotoSheetOpen(true)}
            pending={busy || leaving !== null}
            busyLabel={leaving !== null ? "보내는 중이에요" : "답을 기다리는 중이에요"}
            sentLine={leaving !== null}
            lead={
              // 🚨 **대화가 있을 때만 선다.** 앱을 끄면 그날 대화가 사라져서(#228 전까지) 빈 대화로
              //    가는 문을 세우지 않는다.
              conversation.turns.length > 0 ? (
                <ChatEntryLink href={chatHref} asking={hasOpenQuestion(conversation)} />
              ) : undefined
            }
          />
        </div>
      }
    >
      <header className="flex items-start gap-3">
        <div className="min-w-0 flex-1">
          {/* 섹션 라벨에 "오늘" 이 또 나온다. 제목과 겹치면 같은 말이 두 번이라 제목만 남긴다. */}
          <PageTitle>{nickname ? `오늘 ${nickname}이` : "오늘"}</PageTitle>
          {home.data ? (
            <p className="text-body-sm text-ink-subtle mt-2">
              지금까지 함께 쌓은 기록 {home.data.observation_count}건
            </p>
          ) : null}
        </div>
        {/* 설정은 자주 가는 곳이 아니라 아래 네비에 칸을 주지 않는다 — 제목 옆 아이콘 하나다. */}
        <IconButton label="설정" onClick={() => router.push(`/child/${childId}/settings`)}>
          <Settings aria-hidden size={ICON_SIZE.md} strokeWidth={ICON_STROKE} />
        </IconButton>
      </header>

      {home.isPending ? (
        <Card>
          <SkeletonBlock label="홈을 불러오는 중" />
        </Card>
      ) : consentBlocked ? (
        // 🚨 403 consent_required 를 일반 실패로 그리면 "다시 시도" 만 누르게 된다 — 다시 시도해도
        //    같은 403 이다. 무엇이 막혔는지 말해야 한다 (§3 에러).
        <ConsentRequiredCard
          childId={childId}
          error={consentBlocked}
          what="오늘 기록을 보여드릴 수 없어요."
        />
      ) : home.isError ? (
        <CardFailed>
          <p>홈을 불러오지 못했어요. 적어주신 기록은 그대로 있어요.</p>
          <Button variant="tertiary" size="compact" className="mt-3" onClick={() => home.refetch()}>
            다시 시도
          </Button>
        </CardFailed>
      ) : home.data ? (
        <HomeBody data={home.data} />
      ) : null}

      {/* 🚨 고른 파일을 여기서 올리지 않는다. 08 이 업로드·스트림·저장을 통째로 소유하고,
          홈은 파일 하나를 스토어에 놓고 넘긴다 — 두 화면이 같은 흐름을 두 벌 갖지 않게. */}
      <PhotoSourceSheet
        open={photoSheetOpen}
        onClose={() => setPhotoSheetOpen(false)}
        onPick={({ file, lane }) => {
          putPhoto(childId, file, lane);
          setPhotoSheetOpen(false);
          router.push(`/child/${childId}/photos`);
        }}
      />
    </Screen>
  );
}

/**
 * 03 → 04 대화로 가는 문 (#226). 🚨 **제안 줄 맨 앞의 알약이다** — 따로 한 줄을 주면 각진 버튼 하나가
 * 알약 줄 위에 혼자 떠 보였다. 같은 줄 · 같은 모양(`PROMPT_PILL`)이라 "들어가는 문들" 로 같이 읽힌다.
 * 🚨 **누르는 색은 뉴트럴이다** — 제안은 누르면 그 도메인 색이 되는데(열릴 화면의 색), 대화는 도메인이 없다.
 * 🚨 **질문은 여기서 보여주지 않는다.** 답하는 자리는 대화 화면이고, 문 이름만 바뀐다 — 그때 글자를
 *    `ink` 로 올려 다른 문보다 먼저 읽히게 한다 (색만이 아니라 문구가 같이 바뀐다).
 */
function ChatEntryLink({ href, asking }: { href: string; asking: boolean }) {
  return (
    <Link
      href={href}
      className={cn(
        PROMPT_PILL,
        "hover:bg-surface-muted active:bg-surface-muted whitespace-nowrap",
      )}
    >
      {/* `chip` 높이(28)의 아이콘 원 — 옆 제안 알약의 도메인 칩과 같은 자리 · 같은 크기다. */}
      <span className="bg-brand-soft text-brand-ink min-h-chip flex aspect-square items-center justify-center rounded-full">
        <MessagesSquare aria-hidden size={ICON_SIZE.sm} strokeWidth={ICON_STROKE} />
      </span>
      <span className={cn("text-body-sm pr-1", asking ? "text-ink" : "text-ink-muted")}>
        {asking ? "답을 기다리는 질문이 있어요" : "오늘 대화 이어보기"}
      </span>
    </Link>
  );
}

function HomeBody({ data }: { data: HomeResponse }) {
  // 🚨 기억이 0건이면 빈 상태다. 경보가 아니라 건수를 그대로 보여준다 (문서 §7).
  if (data.highlight === null && data.observation_count === 0) {
    return (
      <EmptyState
        icon={NotebookPen}
        title="아래에 한 줄 적으면 여기에 쌓여요"
        description="기록이 없으면 제안도 만들지 않아요. 지어내지 않으려고요."
        count={0}
      />
    );
  }

  return (
    <div className="flex flex-col gap-6">
      <Counts week={data.week_count} upcoming={data.upcoming_count} />

      {data.today.length > 0 ? (
        <Section label="오늘">
          <Card className="flex flex-col gap-3">
            {data.today.map((item) =>
              item.kind === "meal" ? (
                <TodayRow key={item.title} icon={Utensils} title={item.title} note={item.origin} />
              ) : (
                <TodayRow key={item.event_id} icon={CalendarDays} title={item.title} />
              ),
            )}
          </Card>
        </Section>
      ) : null}

      {data.highlight ? (
        <Section label="눈여겨볼 것">
          <Card tone="accent">
            <div className="flex items-start gap-3">
              <IconTile icon={Repeat} />
              <div className="min-w-0">
                <p className="text-body text-ink">{data.highlight.text}</p>
                {/* 🚨 승격 이유는 서버가 만든 문구다. 프론트에서 횟수를 세지 않는다. */}
                <p className="text-body-sm text-ink-muted mt-1">{data.highlight.state_reason}</p>
              </div>
            </div>
          </Card>
        </Section>
      ) : null}
    </div>
  );
}

/** 섹션 제목. 🚨 브랜드색 라벨이라 화면에 초록이 규칙적으로 들어온다 (문서 §2-2 "canvas 위 브랜드 텍스트"). */
function Section({ label, children }: { label: string; children: ReactNode }) {
  return (
    <section>
      <h2 className="text-label text-brand">{label}</h2>
      <div className="mt-2">{children}</div>
    </section>
  );
}

/**
 * 이번 주 기록 · 다가오는 일정.
 *
 * 카드 두 장을 나란히 놓지 않고 **한 장 안에서 나눈다** — 그리드는 1열 고정이다 (문서 §5).
 * 숫자는 `brand` 다. 부모가 화면에서 제일 먼저 보는 값이라 여기가 브랜드색이 설 자리다.
 */
function Counts({ week, upcoming }: { week: number; upcoming: number }) {
  return (
    <Card className="flex items-stretch gap-4">
      <Count value={week} label="이번 주 기록" />
      <span aria-hidden className="bg-line w-px self-stretch" />
      <Count value={upcoming} label="다가오는 일정" />
    </Card>
  );
}

function Count({ value, label }: { value: number; label: string }) {
  return (
    <div className="flex-1">
      <p className="text-display text-brand">{value}</p>
      <p className="text-caption text-ink-subtle mt-1">{label}</p>
    </div>
  );
}

function TodayRow({ icon, title, note }: { icon: LucideIcon; title: string; note?: string }) {
  return (
    <div className="flex items-center gap-3">
      <IconTile icon={icon} />
      <div className="min-w-0">
        <p className="text-body-sm text-ink">{title}</p>
        {note ? <p className="text-caption text-ink-subtle mt-0.5">{note}</p> : null}
      </div>
    </div>
  );
}
