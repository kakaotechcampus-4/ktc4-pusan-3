"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import {
  CalendarDays,
  NotebookPen,
  Repeat,
  Settings,
  Utensils,
  type LucideIcon,
} from "lucide-react";
import { useRouter } from "next/navigation";
import { useState, type ReactNode } from "react";

import { AuthGate } from "@/components/auth-gate";
import { ChildNav } from "@/components/child-nav";
import { ConsentRequiredCard } from "@/components/consent-required-card";
import { HomeComposer } from "@/components/home-composer";
import { RunProgress, RunResult } from "@/components/run-result";
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
import { useRunStream } from "@/hooks/use-run-stream";
import { useDraftStore, useDraftText } from "@/stores/draft";
import {
  api,
  isApiError,
  qk,
  submitInput,
  type Agent,
  type HomeResponse,
  type InputRequest,
  type Me,
} from "@/lib/api";
import { useIdempotencyKey } from "@/lib/api/use-idempotency-key";

/**
 * 03 홈 · 한 줄 입력 + 04 진행 · 저장 결과.
 *
 * 🚨 **04 를 별도 라우트로 두지 않는다.** 화면을 벗어나면 `useRunStream` 이 스트림을 끊는데,
 *    `failed` 일 때 입력창에 원문을 되돌려 놓아야 한다 (apps/web/CLAUDE.md §3).
 *    그래서 run 이 도는 동안 같은 컴포넌트 안에서 본문만 바꿔 그린다.
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
  /** 05 로 넘길 때 같이 보낸다 — 어느 입력에서 나온 제안인지 서버가 알아야 한다. */
  const [runId, setRunId] = useState<string | null>(null);
  const run = useRunStream(childId);

  const home = useQuery({
    queryKey: qk.home(childId),
    queryFn: () => api.get<HomeResponse>(`/children/${childId}/home`),
  });

  // 🚨 이름은 스토어에 캐시하지 않는다. 개인정보는 화면이 필요할 때 Query 로 가져온다 (§3).
  const me = useQuery({ queryKey: qk.me(), queryFn: () => api.get<Me>("/me") });
  const nickname = me.data?.children.find((c) => c.child_id === childId)?.nickname;

  /**
   * 🚨 **재시도할 때 키를 새로 만들지 않는다** — 같은 키를 다시 보내는 게 중복 저장을 막는다.
   *    `mutationFn` 안에서 `newIdempotencyKey()` 를 부르면 재시도마다 새 키가 나가서
   *    키를 붙인 의미가 통째로 사라진다 (`use-idempotency-key.ts`).
   */
  const idempotencyKey = useIdempotencyKey();

  const submit = useMutation({
    mutationFn: () => {
      const body: InputRequest = { text: text.trim(), source: "home_input" };
      return submitInput(childId, body, idempotencyKey.current());
    },
    onSuccess: (res) => {
      setRunId(res.run_id);
      run.start(res.run_id);
    },
  });

  // 🚨 실패해도 입력창에 원문이 남는 방법은 **지우지 않는 것**이다 (apps/web/CLAUDE.md §3).
  //    `failed` 이벤트의 `raw_text` 로 되돌리는 방법도 있지만, 네트워크가 끊기면 그 값이 안 온다 —
  //    원문의 정본은 draft 스토어고, 성공했을 때만 비운다 (아래 closeRun).
  /** 결과 화면을 닫고 홈으로. 성공이면 입력창을 비운다 — 실패면 원문을 남긴다. */
  function closeRun() {
    const failed = run.state.status === "failed";
    run.reset();
    submit.reset();
    // 🚨 여기서 키를 넘긴다. 실패 뒤 "수정할게요" 로 닫으면 **다음 요청은 본문이 다르고**,
    //    같은 키에 다른 본문을 보내면 422 idempotency_key_reuse 다. 재시도(같은 본문)는
    //    이 함수를 거치지 않고 `retry()` 로 가서 같은 키를 그대로 쓴다.
    idempotencyKey.rotate();
    setRunId(null);
    if (!failed) clearDraft(childId);
  }

  function retry() {
    run.reset();
    // 같은 입력의 재시도다. Idempotency-Key 를 유지한 채 다시 보낸다.
    submit.mutate();
  }

  const consentBlocked = isApiError(home.error, "consent_required") ? home.error : null;

  function goToSuggestions(agents: Agent[]) {
    // run 이 끝난 뒤에만 부른다 — 저장이 이미 끝나서 여기서 스트림이 끊겨도 잃을 것이 없다.
    const params = new URLSearchParams({ agents: agents.join(",") });
    if (runId) params.set("run", runId);
    router.push(`/child/${childId}/suggestions?${params.toString()}`);
  }

  if (run.state.status === "streaming") {
    return (
      <Screen className="gap-6">
        <RunProgress state={run.state} />
      </Screen>
    );
  }

  if (run.state.status !== "idle") {
    return (
      <Screen className="gap-6">
        <RunResult
          state={run.state}
          inputText={text}
          onRetry={retry}
          onEdit={closeRun}
          onDone={closeRun}
          onPickOffer={goToSuggestions}
        />
      </Screen>
    );
  }

  return (
    <Screen
      className="gap-5"
      nav={<ChildNav active="home" />}
      bottomBar={
        <div className="flex flex-col gap-2">
          {submit.isError ? (
            <CardFailed>
              <p>{submit.error instanceof Error ? submit.error.message : "보내지 못했어요."}</p>
              <Button
                variant="tertiary"
                size="compact"
                className="mt-3"
                onClick={() => submit.mutate()}
              >
                다시 시도
              </Button>
            </CardFailed>
          ) : null}
          <HomeComposer
            value={text}
            onChange={setText}
            onSubmit={() => submit.mutate()}
            prompts={home.data?.agent_prompts ?? []}
            onPickPrompt={(agent) => goToSuggestions([agent])}
            pending={submit.isPending}
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
    </Screen>
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
