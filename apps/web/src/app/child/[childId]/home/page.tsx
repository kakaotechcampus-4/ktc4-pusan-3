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
import { PhotoSourceSheet } from "@/components/photo-source-sheet";
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
import { isRunConfirmed, useRunStream } from "@/hooks/use-run-stream";
import { useDraftStore, useDraftText } from "@/stores/draft";
import { usePendingQuestion, usePendingQuestionStore } from "@/stores/pending-question";
import { usePhotoDraftStore } from "@/stores/photo-draft";
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
  /**
   * 사진은 **여기서 고르고 08 에서 확인한다.** 고르는 것은 두 갈래 한 번이라 화면을 따로
   * 두지 않고(`PhotoSourceSheet`), 고른 파일은 라우트를 못 넘어가서 스토어로 넘긴다
   * (`stores/photo-draft.ts` — 메모리 전용).
   */
  const [photoSheetOpen, setPhotoSheetOpen] = useState(false);
  const putPhoto = usePhotoDraftStore((s) => s.putPhoto);
  const run = useRunStream(childId);

  /**
   * Memory 가 되물은 질문. 04 결과에서 "이어서 적기" 를 누르면 여기로 따라온다 —
   * run 은 그 순간 리셋되므로 질문이 run 상태에 남아 있으면 같이 사라진다 (#141).
   */
  const pendingQuestion = usePendingQuestion(childId);
  const setQuestion = usePendingQuestionStore((s) => s.setQuestion);
  const clearQuestion = usePendingQuestionStore((s) => s.clearQuestion);

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
      const body: InputRequest = {
        text: text.trim(),
        source: "home_input",
        // 🚨 되묻기에 답하는 중이면 **어느 run 에 대한 답인지**를 싣는다 (#158 리뷰).
        //    원문을 다시 보내지 않는 이유는 `stores/pending-question.ts` 에 있다.
        ...(pendingQuestion ? { reply_to: pendingQuestion.runId } : {}),
      };
      // 🚨 키를 **본문에 묶는다.** 같은 본문의 재시도는 같은 키(중복 저장 방지), 고쳐 쓴 본문은
      //    새 키다. 서버가 처리했는데 응답만 유실되면 화면은 실패로 보이고 보호자는 한 줄을
      //    고쳐서 다시 보내는데, 키가 그대로면 "같은 키 · 다른 본문" 이라 계속 422 다 (PR #71 리뷰).
      // 🚨 **`text` 만 묶으면 안 된다.** 서버는 본문 **전체**로 같은 요청인지 보므로(idempotency-v1),
      //    같은 답을 다른 질문에 보내면 "같은 키 · 다른 본문" 이라 422 다 — `reply_to` 도 함께 묶는다.
      return submitInput(childId, body, idempotencyKey.current(keyPayload(body)));
    },
    onSuccess: (res) => {
      setRunId(res.run_id);
      run.start(res.run_id);
      // 되묻기에 답하러 왔든 다른 이야기를 적었든, 이 한 줄로 그 질문은 끝났다.
      clearQuestion(childId);
    },
  });

  // 🚨 실패해도 입력창에 원문이 남는 방법은 **지우지 않는 것**이다 (apps/web/CLAUDE.md §3).
  //    `failed` 이벤트의 `raw_text` 로 되돌리는 방법도 있지만, 네트워크가 끊기면 그 값이 안 온다 —
  //    원문의 정본은 draft 스토어고, 서버가 끝을 말했을 때만 비운다 (아래 closeRun).
  /** 결과 화면을 닫고 홈으로. 🚨 서버가 끝을 말한 경우에만 원문과 키를 비운다. */
  function closeRun() {
    // 🚨 `failed` 만 보면 안 된다. 20초 침묵·끊긴 스트림(`unconfirmed`)도 **저장됐는지 모르는**
    //    상태라, 여기서 비우면 확인되지 않은 채로 보호자가 적은 말이 사라진다 (PR #71 리뷰).
    const confirmed = isRunConfirmed(run.state.status);
    run.reset();
    submit.reset();
    setRunId(null);
    if (!confirmed) return;

    // 여기까지 왔으면 이 입력은 끝났다. 다음 한 줄은 새 동작이라 새 키를 쓴다.
    // 🚨 반대로 실패·미확인 상태에서는 키를 갈지 않는다 — 서버가 이미 저장했을 수 있는 한 줄을
    //    새 키로 다시 보내면 그때 두 번 저장된다. 본문을 고쳐 쓰는 경우는 키가 본문에 묶여 있어
    //    (`current(body.text)`) 저절로 새 키가 나간다.
    idempotencyKey.rotate();
    clearDraft(childId);
  }

  function retry() {
    run.reset();
    // 같은 입력의 재시도다. Idempotency-Key 를 유지한 채 다시 보낸다.
    submit.mutate();
  }

  /**
   * 되묻는 질문에 답하러 03 홈으로 돌아간다 (#141 · #158 리뷰).
   *
   * 🚨 **원문을 입력창에 되돌리지 않는다.** 한동안 되돌려 놨었다 — 답은 새 한 줄이라 앞의 run 을
   *    모르니 맥락을 화면이 만들어 주려던 것이었다. 그런데 **일부는 저장되고 질문이 같이 오는
   *    run** 이 있어서("계란 잘 먹었어. 요즘 기침해" → 계란 저장 + 기침 되묻기 ·
   *    `apps/api/app/agents/pipeline.py`), 원문을 다시 보내면 **계란이 두 번 저장된다.**
   *    7일 승격 집계가 한 번의 관찰을 두 번으로 세게 되므로 최상위 §2 가 깨진다 (#154).
   *    맥락은 서버가 `reply_to` 로 찾는다 (위 `submit`).
   *
   * 🚨 **`runId` 를 질문과 함께 넘긴다.** `closeRun()` 이 `setRunId(null)` 로 지우므로 그 전에
   *    붙들어야 한다. 순서가 바뀌면 `reply_to` 가 비어서 답이 맥락 없는 새 입력이 된다.
   */
  function answerQuestion(question: string) {
    // 🚨 `closeRun()` 이 지우기 전에 붙든다.
    const answeringRunId = runId;
    if (answeringRunId) setQuestion(childId, { text: question, runId: answeringRunId });
    closeRun();
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
          childId={childId}
          inputText={text}
          onRetry={retry}
          onEdit={closeRun}
          onDone={closeRun}
          onPickOffer={goToSuggestions}
          onAnswerQuestion={answerQuestion}
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
          {/* 🚨 **`accent` 가 아니다.** 이 화면의 accent 한 장은 "눈여겨볼 것" 이 이미 쓰고 있다
              (`HomeBody`) — 두 장이 되면 강조가 아니라 장식이 된다 (`components/ui/card.tsx`). */}
          {pendingQuestion ? (
            <Card>
              <p className="text-caption text-ink-subtle">한 가지만 더</p>
              {/* 🚨 LLM 이 만든 문장이라 HTML 로 그리지 않는다 (apps/web/CLAUDE.md §4). */}
              <p className="text-body-sm text-ink mt-1">{pendingQuestion.text}</p>
              {/* 🚨 **답만 적으라고 말한다.** 앞서 적은 말을 다시 쓰게 하면 이미 저장된 조각이
                  또 저장된다 (#158 리뷰) — 앞 이야기는 서버가 `reply_to` 로 찾는다. */}
              <p className="text-caption text-ink-subtle mt-2">
                이 질문에 대한 답만 적어주세요. 앞서 적어주신 말은 저장돼 있어요.
              </p>
              <Button
                variant="tertiary"
                size="compact"
                className="mt-3"
                onClick={() => clearQuestion(childId)}
              >
                나중에 할게요
              </Button>
            </Card>
          ) : null}
          {submit.isError ? (
            <SubmitErrorCard error={submit.error} onRetry={() => submit.mutate()} />
          ) : null}
          <HomeComposer
            value={text}
            onChange={setText}
            onSubmit={() => submit.mutate()}
            prompts={home.data?.agent_prompts ?? []}
            onPickPrompt={(agent) => goToSuggestions([agent])}
            onPickPhoto={() => setPhotoSheetOpen(true)}
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
 * Idempotency-Key 를 묶을 본문 지문.
 *
 * 🚨 **`text` 만으로는 부족하다.** 요청 지문은 `method + 경로 + 요청 본문` 이라
 *    (`docs/api/idempotency-v1.md` §3-2), "네" 라는 같은 답을 서로 다른 질문에 보내면
 *    같은 키에 다른 본문(`reply_to`)이 실려 `422 idempotency_key_reuse` 가 된다.
 * 🚨 필드를 더하면 **여기도 같이** 더한다 — 빠뜨리면 그 필드만 바뀐 요청이 조용히 같은 키로 나간다.
 */
function keyPayload(body: InputRequest): string {
  return `${body.reply_to ?? ""}|${body.source}|${body.text}`;
}

/**
 * 한 줄을 보내지 못했을 때 (#147).
 *
 * 🚨 **`daily_input_limit` 에는 "다시 시도" 를 두지 않는다.** 같은 본문은 같은 Idempotency-Key 로
 *    나가고(`use-idempotency-key.ts`), 한도는 한국 시간 자정에 풀린다 — 버튼을 세워 두면 부모가
 *    누를 때마다 같은 429 를 받는다. 누르면 같은 실패가 나오는 버튼을 만들지 않는다
 *    (apps/web/CLAUDE.md §3 에러 — `consent_required` 를 일반 실패로 그리지 않는 것과 같은 이유다).
 *
 * 🚨 **문구는 서버 것을 그대로 쓴다.** 하루 몇 번인지는 서버 설정값이라 바뀐다 (#147) — 화면이
 *    숫자를 따로 적으면 그날부터 둘이 어긋나고, 틀린 쪽은 언제나 화면이다.
 */
function SubmitErrorCard({ error, onRetry }: { error: unknown; onRetry: () => void }) {
  const limited = isApiError(error, "daily_input_limit");

  return (
    <CardFailed>
      <p>{error instanceof Error ? error.message : "보내지 못했어요."}</p>
      {limited ? (
        // 🚨 원문은 지우지 않는다 — 내일 이어서 보낼 한 줄이다 (`closeRun` 과 같은 규칙).
        <p className="text-caption text-ink-subtle mt-2">
          적어주신 말은 입력창에 그대로 남겨뒀어요.
        </p>
      ) : (
        <Button variant="tertiary" size="compact" className="mt-3" onClick={onRetry}>
          다시 시도
        </Button>
      )}
    </CardFailed>
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
