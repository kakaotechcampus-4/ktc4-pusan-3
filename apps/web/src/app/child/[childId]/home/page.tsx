"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { CalendarDays, NotebookPen, Utensils } from "lucide-react";
import { useRouter } from "next/navigation";
import { useRef, useState, type ReactNode } from "react";

import { AuthGate } from "@/components/auth-gate";
import { RunProgress, RunResult } from "@/components/run-result";
import { Button } from "@/components/ui/button";
import { Card, CardFailed } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { ICON_SIZE, ICON_STROKE } from "@/components/ui/icon";
import { PageTitle } from "@/components/ui/page-title";
import { Screen } from "@/components/ui/screen";
import { SkeletonBlock } from "@/components/ui/skeleton";
import { Spinner } from "@/components/ui/spinner";
import { TextArea } from "@/components/ui/text-area";
import { useChildId } from "@/hooks/use-child-id";
import { useRunStream } from "@/hooks/use-run-stream";
import {
  api,
  newIdempotencyKey,
  qk,
  type Agent,
  type CreateInputRequest,
  type CreateInputResponse,
  type HomeResponse,
  type Me,
} from "@/lib/api";

/**
 * 03 홈 · 한 줄 입력 + 04 진행 · 저장 결과.
 *
 * 🚨 **04 를 별도 라우트로 두지 않는다.** 화면을 벗어나면 `useRunStream` 이 스트림을 끊는데,
 *    `failed` 일 때 입력창에 되돌릴 **원문의 정본은 이 화면이 들고 있는 `text`** 다
 *    (apps/web/CLAUDE.md §3). 라우트를 나누면 그 값이 언마운트와 함께 죽는다.
 *    그래서 run 이 도는 동안 같은 컴포넌트 안에서 본문만 바꿔 그린다.
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

  const [text, setText] = useState("");
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

  /** 🚨 재시도할 때 키를 새로 만들지 않는다 — 같은 키를 다시 보내는 게 중복 저장을 막는다. */
  const idempotencyKey = useRef<string | null>(null);

  const submit = useMutation({
    mutationFn: () => {
      idempotencyKey.current ??= newIdempotencyKey();
      const body: CreateInputRequest = { text: text.trim(), source: "home_input" };
      return api.post<CreateInputResponse>(`/children/${childId}/inputs`, body, {
        idempotencyKey: idempotencyKey.current,
      });
    },
    onSuccess: (res) => {
      setRunId(res.run_id);
      run.start(res.run_id);
    },
  });

  // 🚨 실패해도 입력창에 원문이 남는 방법은 **지우지 않는 것**이다 (apps/web/CLAUDE.md §3).
  //    `failed` 이벤트의 `raw_text` 로 되돌리는 방법도 있지만, 네트워크가 끊기면 그 값이 안 온다 —
  //    원문의 정본은 이 화면의 `text` 고, 성공했을 때만 비운다 (아래 closeRun).
  /** 결과 화면을 닫고 홈으로. 성공이면 입력창을 비운다 — 실패면 원문을 남긴다. */
  function closeRun() {
    const failed = run.state.status === "failed";
    run.reset();
    submit.reset();
    idempotencyKey.current = null;
    setRunId(null);
    if (!failed) setText("");
  }

  function retry() {
    run.reset();
    // 같은 입력의 재시도다. Idempotency-Key 를 유지한 채 다시 보낸다.
    submit.mutate();
  }

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
    <Screen className="gap-5">
      <header>
        <PageTitle>{nickname ? `오늘 ${nickname}이` : "오늘"}</PageTitle>
        {home.data ? (
          <p className="text-body-sm text-ink-subtle mt-2">기억 {home.data.observation_count}건</p>
        ) : null}
      </header>

      {home.isPending ? (
        <Card>
          <SkeletonBlock label="홈을 불러오는 중" />
        </Card>
      ) : home.isError ? (
        <CardFailed>
          <p>홈을 불러오지 못했어요. 적어주신 기록은 그대로 있어요.</p>
          <Button variant="tertiary" className="mt-1 -ml-2" onClick={() => home.refetch()}>
            다시 시도
          </Button>
        </CardFailed>
      ) : home.data ? (
        <HomeBody data={home.data} />
      ) : null}

      <section className="mt-auto flex flex-col gap-3 pt-4">
        <TextArea
          label="오늘 있었던 일 한 줄"
          hint="한 줄만 적어도 돼요. 정리하지 않아도 괜찮아요."
          placeholder="예: 저녁에 계란말이를 또 찾았어요"
          value={text}
          onChange={(e) => setText(e.target.value)}
          disabled={submit.isPending}
        />

        {submit.isError ? (
          <CardFailed>
            <p>{submit.error instanceof Error ? submit.error.message : "보내지 못했어요."}</p>
            <Button variant="tertiary" className="mt-1 -ml-2" onClick={() => submit.mutate()}>
              다시 시도
            </Button>
          </CardFailed>
        ) : null}

        <Button block onClick={() => submit.mutate()} disabled={submit.isPending || !text.trim()}>
          {submit.isPending ? <Spinner /> : null}
          {submit.isPending ? "보내는 중…" : "이 이야기 남기기"}
        </Button>
      </section>
    </Screen>
  );
}

function HomeBody({ data }: { data: HomeResponse }) {
  const router = useRouter();
  const childId = useChildId();

  // 🚨 기억이 0건이면 빈 상태다. 경보가 아니라 건수를 그대로 보여준다 (문서 §7).
  if (data.highlight === null && data.observation_count === 0) {
    return (
      <EmptyState
        icon={NotebookPen}
        title="아래에 한 줄 적으면 여기에 쌓여요"
        description="기억이 없으면 제안도 만들지 않아요. 지어내지 않으려고요."
        count={0}
      />
    );
  }

  return (
    <div className="flex flex-col gap-5">
      <div className="flex gap-3">
        <CountCard label="이번 주 기록" value={data.week_count} />
        <CountCard label="다가오는 일정" value={data.upcoming_count} />
      </div>

      {data.today.length > 0 ? (
        <section className="flex flex-col gap-3">
          <h2 className="text-section text-ink">오늘</h2>
          {data.today.map((item) =>
            item.kind === "meal" ? (
              <TodayCard key={item.title} icon={<Utensils {...TODAY_ICON} />} title={item.title}>
                {item.origin}
              </TodayCard>
            ) : (
              <TodayCard
                key={item.event_id}
                icon={<CalendarDays {...TODAY_ICON} />}
                title={item.title}
              />
            ),
          )}
        </section>
      ) : null}

      {data.highlight ? (
        <section className="flex flex-col gap-3">
          <h2 className="text-section text-ink">눈여겨볼 것</h2>
          <Card>
            <p className="text-body text-ink">{data.highlight.text}</p>
            {/* 🚨 승격 이유는 서버가 만든 문구다. 프론트에서 횟수를 세지 않는다. */}
            <p className="text-body-sm text-ink-muted mt-1">{data.highlight.state_reason}</p>
          </Card>
        </section>
      ) : null}

      {data.agent_prompts.length > 0 ? (
        <section className="flex flex-col gap-3">
          <h2 className="text-section text-ink">지금 도와드릴 수 있는 것</h2>
          {/* 🚨 시각대 규칙(F-15)으로 서버가 만든 목록이다. 프론트가 고르지 않는다.
              primary 를 쓰지 않는 이유는 아래 입력 버튼이 이 화면의 다음 행동이기 때문이다. */}
          {data.agent_prompts.slice(0, 2).map((prompt) => (
            <Button
              key={prompt.agent}
              variant="secondary"
              block
              onClick={() => router.push(`/child/${childId}/suggestions?agents=${prompt.agent}`)}
            >
              {prompt.text}
            </Button>
          ))}
        </section>
      ) : null}
    </div>
  );
}

const TODAY_ICON = {
  "aria-hidden": true,
  size: ICON_SIZE.md,
  strokeWidth: ICON_STROKE,
  className: "text-ink-subtle shrink-0",
} as const;

function CountCard({ label, value }: { label: string; value: number }) {
  return (
    <div className="border-line rounded-card bg-surface flex-1 border p-4">
      <p className="text-title text-ink">{value}</p>
      <p className="text-caption text-ink-subtle mt-1">{label}</p>
    </div>
  );
}

function TodayCard({
  icon,
  title,
  children,
}: {
  icon: ReactNode;
  title: string;
  children?: ReactNode;
}) {
  return (
    <Card>
      <div className="flex items-start gap-2.5">
        {icon}
        <div>
          <p className="text-body text-ink">{title}</p>
          {children ? <p className="text-caption text-ink-subtle mt-1">{children}</p> : null}
        </div>
      </div>
    </Card>
  );
}
