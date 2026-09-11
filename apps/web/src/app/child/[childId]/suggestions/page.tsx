"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

import { ApprovalSheet } from "@/components/approval-sheet";
import { AuthGate } from "@/components/auth-gate";
import { domainLabel } from "@/components/domain-chip";
import { HealthSuggestionCard, SuggestionCard } from "@/components/suggestion-card";
import { Banner } from "@/components/ui/banner";
import { Button } from "@/components/ui/button";
import { Card, CardFailed } from "@/components/ui/card";
import { Chip, ChipRow } from "@/components/ui/chip";
import { PageTitle } from "@/components/ui/page-title";
import { Screen } from "@/components/ui/screen";
import { SkeletonBlock } from "@/components/ui/skeleton";
import { useChildId } from "@/hooks/use-child-id";
import {
  AGENTS,
  api,
  isApiError,
  qk,
  type Agent,
  type AnswerRequest,
  type CreateEventRequest,
  type CreateEventResponse,
  type Scarcity,
  type Suggestion,
  type SuggestionsRequest,
  type SuggestionsResponse,
} from "@/lib/api";

/**
 * 05 제안 후보.
 *
 * 🚨 **개인화 추천에는 근거가 반드시 붙는다.** `evidence` 가 빈 suggestion 은 서버가 버리고
 *    `scarcity` 로 내린다 — "근거 없음" 카드를 그릴 수 있는 경로를 만들지 않는다 (CLAUDE.md §2).
 * 🚨 **근거가 부족하면 되묻는 질문은 1개**다. 복수 질문을 만들지 않는다.
 * 🚨 **Agent 가 규칙에 막혔으면(`guards`) 그 도메인 제안을 만들지 않는다.** 알레르기를 모르면
 *    Food Agent 는 기본값으로 넘기는 게 아니라 **실행 자체가 막힌다.**
 * 🚨 **성공과 실패를 한 화면에 섞는다** (NF-06). 요청한 Agent 중 결과가 없는 쪽은
 *    `CardFailed` 로 같은 화면에 남긴다 — 전체를 실패 화면으로 덮지 않는다.
 *
 * ⚠️ 일반 추천 카드(`card-general`)는 아직 없다. 계약서 v1 의 `scarcity` 응답에는
 *    "또래 기준 일반 추천" 을 실을 자리가 없고, 개인화와 일반을 **타입으로 구분**하라는
 *    CLAUDE.md §2 의 필드도 아직 계약서에 없다. 없는 필드를 프론트가 지어내면 그 규칙을
 *    UI 로 덮는 것이라, 지금은 계약서대로 "기록이 부족하다" 를 말하고 질문 1개를 드린다.
 */
export default function SuggestionsPage() {
  return (
    // 🚨 `useSearchParams()` 는 Suspense 경계 안에 있어야 한다 — 없으면 프리렌더가 빌드에서 막힌다.
    //    fallback 은 비워 둔다. AuthGate 도 hydrate 전에는 아무것도 그리지 않아서, 여기서만
    //    스켈레톤을 띄우면 로그인 확인 전에 화면이 한 번 깜빡인다.
    <Suspense fallback={null}>
      <AuthGate>
        <SuggestionsScreen />
      </AuthGate>
    </Suspense>
  );
}

function isAgent(value: string): value is Agent {
  return (AGENTS as readonly string[]).includes(value);
}

function SuggestionsScreen() {
  const childId = useChildId();
  const router = useRouter();
  const searchParams = useSearchParams();

  const runId = searchParams.get("run");
  /** 🚨 최대 2개다 (NF-01). URL 로 더 넘어와도 잘라서 보낸다. */
  const agents = (searchParams.get("agents") ?? "").split(",").filter(isAgent).slice(0, 2);

  const [dismissed, setDismissed] = useState<string[]>([]);
  const [approving, setApproving] = useState<{
    suggestion: Suggestion;
    draft: CreateEventResponse;
  } | null>(null);

  const suggestions = useQuery({
    queryKey: qk.suggestions(childId, runId, agents),
    enabled: agents.length > 0,
    // Agent 를 두 번 돌리지 않는다 — NF-01 이 model call 을 센다. 뒤로 갔다 와도 같은 화면이다.
    staleTime: Infinity,
    queryFn: () => {
      const body: SuggestionsRequest = { agents, ...(runId ? { run_id: runId } : {}) };
      return api.post<SuggestionsResponse>(`/children/${childId}/suggestions`, body);
    },
  });

  /** 승인 게이트가 아니다 — 초안은 24시간 뒤 만료되는 되돌릴 수 있는 상태다. */
  const createDraft = useMutation({
    mutationFn: (suggestion: Suggestion) => {
      const body: CreateEventRequest = { title: suggestion.content };
      return api
        .post<CreateEventResponse>(`/suggestions/${suggestion.id}/event`, body)
        .then((draft) => ({ suggestion, draft }));
    },
    onSuccess: (result) => setApproving(result),
  });

  const data = suggestions.data;
  const visible = data?.suggestions.filter((s) => !dismissed.includes(s.id)) ?? [];
  const blockedByGuard = new Set(data?.guards.flatMap((g) => g.blocked_agents) ?? []);
  /**
   * 요청했는데 결과도 없고 규칙에 막힌 것도 아닌 Agent = 이번에 실패한 쪽이다 (NF-06).
   * 응답이 오기 전과 `scarcity` 는 제외한다 — 기다리는 중인 것과 근거가 부족한 것은 실패가 아니다.
   */
  const failedAgents =
    data && !data.scarcity
      ? agents.filter(
          (agent) => !blockedByGuard.has(agent) && !data.suggestions.some((s) => s.agent === agent),
        )
      : [];

  const consentBlocked = isApiError(suggestions.error, "consent_required")
    ? suggestions.error
    : null;

  return (
    <Screen className="gap-5">
      <header>
        <PageTitle>제안 후보</PageTitle>
        {data ? <p className="text-body-sm text-ink-muted mt-2">{data.looked_at}</p> : null}
      </header>

      {/* 🚨 배너는 화면 최상단 **한 곳**에만 둔다 (문서 §7). guard 가 여럿이어도 배너는 하나고,
          안에서 줄로 나눈다. 도메인 이름은 `blocked_agents` 에서 만든다 — 문구에 박지 않는다. */}
      {data && data.guards.length > 0 ? (
        <Banner
          tone="caution"
          title={`${[...blockedByGuard].map(domainLabel).join(", ")} 제안을 만들지 않았어요`}
        >
          {data.guards.map((guard) => (
            <p key={guard.code}>{guard.message}</p>
          ))}
          <p className="text-caption mt-1">
            설정에서 알려주시면 이 배너가 사라져요. 설정 화면은 다음 이슈예요.
          </p>
        </Banner>
      ) : null}

      {agents.length === 0 ? (
        <Card>
          <p className="text-body text-ink">어떤 도움이 필요한지 고르지 않으셨어요</p>
          <p className="text-body-sm text-ink-muted mt-2">
            홈에서 도와드릴 것을 고르면 그 주제로 준비해요.
          </p>
        </Card>
      ) : suggestions.isPending ? (
        <Card>
          <SkeletonBlock label="제안을 준비하는 중" />
        </Card>
      ) : consentBlocked ? (
        <Card>
          <p className="text-body text-ink">먼저 동의가 필요해요</p>
          <p className="text-body-sm text-ink-muted mt-2">
            저장은 아직 하나도 되지 않았어요. 동의를 마치면 다시 받아볼 수 있어요.
          </p>
          <p className="text-caption text-ink-subtle mt-2">
            동의 화면은 아직 없어요 (/child/{childId}/{consentBlocked.consentDeeplink})
          </p>
        </Card>
      ) : suggestions.isError ? (
        // 🚨 llm_unavailable 을 기본값으로 대체하지 않는다. 실패했다고 화면에 말한다.
        <CardFailed>
          <p>
            {suggestions.error instanceof Error
              ? suggestions.error.message
              : "제안을 준비하지 못했어요."}
          </p>
          <Button variant="tertiary" className="mt-1 -ml-2" onClick={() => suggestions.refetch()}>
            다시 시도
          </Button>
        </CardFailed>
      ) : null}

      {visible.map((suggestion) =>
        suggestion.agent === "health" ? (
          <HealthSuggestionCard key={suggestion.id} suggestion={suggestion} />
        ) : (
          <SuggestionCard
            key={suggestion.id}
            suggestion={suggestion}
            busy={createDraft.isPending}
            onApprove={() => createDraft.mutate(suggestion)}
            onReject={() => setDismissed((prev) => [...prev, suggestion.id])}
          />
        ),
      )}

      {createDraft.isError ? (
        <CardFailed>
          <p>
            {createDraft.error instanceof Error
              ? createDraft.error.message
              : "저장될 내용을 만들지 못했어요."}
          </p>
          <p className="mt-1">아직 아무것도 넣지 않았어요.</p>
        </CardFailed>
      ) : null}

      {/* NF-06 — 실패한 쪽도 같은 화면에 남긴다. 빨강이 아니다. */}
      {failedAgents.map((agent) => (
        <CardFailed key={agent}>
          <p>{domainLabel(agent)} 쪽은 이번에 준비하지 못했어요.</p>
          <Button variant="tertiary" className="mt-1 -ml-2" onClick={() => suggestions.refetch()}>
            다시 시도
          </Button>
        </CardFailed>
      ))}

      {data?.scarcity ? <ScarcityCard childId={childId} scarcity={data.scarcity} /> : null}

      <Button
        variant="secondary"
        block
        className="mt-auto"
        onClick={() => router.push(`/child/${childId}/home`)}
      >
        홈으로
      </Button>

      {approving ? (
        <ApprovalSheet
          open
          onClose={() => {
            setApproving(null);
            createDraft.reset();
          }}
          childId={childId}
          suggestion={approving.suggestion}
          draft={approving.draft}
        />
      ) : null}
    </Screen>
  );
}

/**
 * 근거가 부족할 때. 🚨 **질문은 한 개까지만** 드린다 (CLAUDE.md §2).
 * 쌓인 기록 건수를 숨기지 않고 그대로 보여준다 — 없는 근거로 "우리 아이 맞춤" 인 척하지 않는다.
 */
function ScarcityCard({ childId, scarcity }: { childId: string; scarcity: Scarcity }) {
  const [answered, setAnswered] = useState<string | null>(null);

  const answer = useMutation({
    mutationFn: (value: string) => {
      const body: AnswerRequest = { question_id: scarcity.question.id, answer: value };
      return api.post(`/children/${childId}/answers`, body);
    },
    onSuccess: (_data, value) => setAnswered(value),
  });

  return (
    <Card>
      <h2 className="text-section text-ink">판단할 기록이 부족해요</h2>
      <p className="text-body-sm text-ink-muted mt-1">
        이 주제로 참고할 기록이 {scarcity.count}건뿐이에요. 근거 없이 추천을 만들지는 않을게요.
      </p>

      <p className="text-body text-ink mt-4">{scarcity.question.text}</p>
      <ChipRow>
        {scarcity.question.options.map((option) => (
          <Chip
            key={option}
            selected={answered === option}
            disabled={answer.isPending || answered !== null}
            onClick={() => answer.mutate(option)}
          >
            {option}
          </Chip>
        ))}
      </ChipRow>

      {answer.isError ? (
        <p className="text-body-sm text-ink-muted mt-2">답을 저장하지 못했어요.</p>
      ) : answered ? (
        <p className="text-body-sm text-ink-muted mt-2">
          기록으로 남겼어요. 다음 제안부터 근거로 써요.
        </p>
      ) : null}

      <p className="text-caption text-ink-subtle mt-2">질문은 한 개까지만 드려요.</p>
    </Card>
  );
}
