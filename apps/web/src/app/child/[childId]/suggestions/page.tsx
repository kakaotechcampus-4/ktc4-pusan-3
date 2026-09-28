"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

import { ApprovalSheet } from "@/components/approval-sheet";
import { AuthGate } from "@/components/auth-gate";
import { ConsentRequiredCard } from "@/components/consent-required-card";
import { domainLabel } from "@/components/domain-chip";
import { GeneralSuggestionCard } from "@/components/general-suggestion-card";
import { SuggestionList } from "@/components/suggestion-list";
import { Banner } from "@/components/ui/banner";
import { Button } from "@/components/ui/button";
import { Card, CardFailed } from "@/components/ui/card";
import { Chip, ChipRow } from "@/components/ui/chip";
import { PageTitle } from "@/components/ui/page-title";
import { Screen } from "@/components/ui/screen";
import { SkeletonBlock } from "@/components/ui/skeleton";
import { Spinner } from "@/components/ui/spinner";
import { useChildId } from "@/hooks/use-child-id";
import {
  AGENTS,
  api,
  isApiError,
  qk,
  type Agent,
  type AnswerRequest,
  createEventDrafts,
  type CreateEventDraftsResponse,
  type Scarcity,
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
 * 🚨 **근거가 부족하면 개인화 대신 일반 추천을 그린다** (CLAUDE.md §2). 개인화 목록과
 *    일반 카드는 **다른 컴포넌트 · 다른 타입 · 응답의 다른 필드**다 — 한 곳에 섞으면
 *    언젠가 근거 0건인 것이 개인화로 그려진다. 둘이 한 화면에 같이 나오는 경우는 없다.
 *
 * ⚠️ `general` 필드는 **계약서 v1 에 아직 없다** (types.ts 의 ⚠️). 서버가 안 보내면
 *    일반 카드 없이 예전처럼 질문 1개만 그린다 — 프론트가 또래 기준 추천을 지어내지 않는다.
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

  /**
   * 🚨 **여러 개 고를 수 있다.** 고른 것을 한 번에 초안으로 바꾼다 —
   *    `food` 제안들은 서버가 한 끼로 묶어 초안 1건으로 내려준다 (`types.ts` 참고).
   */
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [approving, setApproving] = useState<CreateEventDraftsResponse | null>(null);

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

  /**
   * 🚨 **승인 게이트가 아니다 — 이 호출은 아무것도 쓰지 않는다** (#121). 응답은 저장된 일정이
   *    아니라 초안 + 사전검사고, 쓰는 것은 시트 안 카드의 제출 하나다.
   *    (만료 문구를 쓰지 않는다: `event.status` 가 없어지면서 서버에 초안 행 자체가 없다 · #118.
   *     24시간 만료가 남아 있는 것은 `suggestion` 쪽이다.)
   *
   * 🚨 **고른 개수와 초안 개수가 다를 수 있다.** 화면은 세지 않고 응답이 준 장수를 그대로 쓴다 —
   *    묶는 판단은 도메인 지식이라 서버가 한다.
   */
  const createDrafts = useMutation({
    mutationFn: (ids: string[]) => createEventDrafts(childId, { suggestion_ids: ids }),
    onSuccess: (result) => setApproving(result),
  });

  const data = suggestions.data;
  const visible = data?.suggestions ?? [];
  /**
   * 또래 기준 일반 추천. 🚨 **`scarcity` 가 있을 때만 그린다** — 개인화 **대신** 나가는 것이라
   * 둘이 한 화면에 같이 서면 무엇이 우리 아이 기준인지가 흐려진다 (CLAUDE.md §2).
   * 서버가 아직 `general` 을 안 보내면 빈 배열이고, 화면은 질문 1개만 그린다.
   */
  const scarcity = data?.scarcity ?? null;
  const general = scarcity ? (data?.general ?? []) : [];
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

  /**
   * 고른 것을 묶음별로 센다. 🚨 **순서는 서버가 준 순서**다 — 목록과 요약이 다른 순서면
   *    보호자가 둘을 맞춰 읽어야 한다.
   */
  const pickedByAgent = visible.reduce<Array<{ agent: Agent; count: number }>>((acc, s) => {
    if (!selectedIds.includes(s.id)) return acc;
    const found = acc.find((g) => g.agent === s.agent);
    if (found) found.count += 1;
    else acc.push({ agent: s.agent, count: 1 });
    return acc;
  }, []);

  const consentBlocked = isApiError(suggestions.error, "consent_required")
    ? suggestions.error
    : null;

  return (
    <Screen className="gap-5">
      <header>
        <PageTitle>제안 후보</PageTitle>
        {/* 🚨 looked_at 앞에 가운뎃점을 하나 더 붙이지 않는다 — 이미 점으로 나뉜 메타
            스트립이고, 띄운 가운뎃점은 줄당 하나까지다 (문서 §4). */}
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
            알레르기를 알려주시면 식사 제안도 함께 준비해요. 알려주는 화면은 준비 중이에요.
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
        <ConsentRequiredCard
          childId={childId}
          error={consentBlocked}
          what="제안을 준비할 수 없어요."
        />
      ) : suggestions.isError ? (
        // 🚨 llm_unavailable 을 기본값으로 대체하지 않는다. 실패했다고 화면에 말한다.
        <CardFailed>
          <p>
            {suggestions.error instanceof Error
              ? suggestions.error.message
              : "제안을 준비하지 못했어요."}
          </p>
          <Button
            variant="tertiary"
            size="compact"
            className="mt-3"
            onClick={() => suggestions.refetch()}
          >
            다시 시도
          </Button>
        </CardFailed>
      ) : null}

      {/* 🚨 후보를 카드 더미로 펼쳐 놓지 않는다. 한 덩어리 목록에서 한 줄씩 훑고, 고른 하나만
          그 자리에서 연다 — 밤에 한 손으로 여는 화면에서 "둘 다 읽고 고르기" 는 인지 노동이다. */}
      <SuggestionList
        suggestions={visible}
        groups={data?.groups ?? []}
        selectedIds={selectedIds}
        busy={createDrafts.isPending}
        onToggleSelect={(suggestion) =>
          setSelectedIds((prev) =>
            prev.includes(suggestion.id)
              ? prev.filter((id) => id !== suggestion.id)
              : [...prev, suggestion.id],
          )
        }
      />

      {/* 🚨 **다음 행동은 여기 한 곳이다** (줄마다 두지 않는다 · `suggestion-list.tsx` 머리말).
          그래서 화면의 `primary` 도 하나다 — §7 의 "목록 항목 예외" 가 필요 없어졌다. */}
      {visible.some((s) => s.agent !== "health") ? (
        <Card>
          <p className="text-section text-ink">이렇게 준비할게요</p>
          {/* 🚨 **묶음별로 센다.** "3건" 만 말하면 보호자는 무엇을 셋 골랐는지 되짚어야 한다 —
              대안이 Agent 당 셋이라 "식사 2가지 · 놀이 1가지" 가 실제로 고른 모양이다. */}
          <dl className="mt-3 flex flex-col gap-2">
            <div className="flex gap-2">
              <dt className="text-label text-ink-subtle w-14 shrink-0">고른 것</dt>
              <dd className="text-body-sm text-ink">
                {selectedIds.length === 0
                  ? "아직 없음"
                  : pickedByAgent
                      .map(({ agent, count }) => `${domainLabel(agent)} ${count}가지`)
                      .join(" · ")}
              </dd>
            </div>
          </dl>

          {/* 🚨 **몇 장이 될지 화면이 말하지 않는다.** food 제안들이 한 끼로 묶이는지는 서버가
              정하는데, 화면이 "N건 만들어요" 라고 먼저 말하면 응답과 다를 때 거짓말이 된다. */}
          <p className="text-body-sm text-ink-muted bg-surface-muted rounded-field mt-3 px-3 py-2">
            고른 제안으로 저장될 내용을 먼저 보여드려요. 승인 전에는 아무것도 넣지 않아요.
          </p>

          {createDrafts.isError ? (
            <CardFailed className="mt-3">
              <p>
                {createDrafts.error instanceof Error
                  ? createDrafts.error.message
                  : "저장될 내용을 만들지 못했어요."}
              </p>
              <p className="mt-1">아직 아무것도 넣지 않았어요.</p>
            </CardFailed>
          ) : null}

          <Button
            block
            className="mt-4"
            disabled={selectedIds.length === 0 || createDrafts.isPending}
            aria-busy={createDrafts.isPending}
            onClick={() => createDrafts.mutate(selectedIds)}
          >
            {createDrafts.isPending ? <Spinner /> : null}
            {createDrafts.isPending
              ? "준비하는 중이에요"
              : selectedIds.length === 0
                ? "고른 것이 없어요"
                : // 🚨 단위를 섞지 않는다 — 제안은 "가지"(한 결정의 대안), 일정은 "건" 이다.
                  //    같은 화면에서 "3건 고름 → 일정 2건" 이 되면 같은 단위가 두 뜻을 나른다.
                  `고른 ${selectedIds.length}가지로 일정 만들기`}
          </Button>
        </Card>
      ) : null}

      {/* NF-06 — 실패한 쪽도 같은 화면에 남긴다. 빨강이 아니다. */}
      {failedAgents.map((agent) => (
        <CardFailed key={agent}>
          <p>{domainLabel(agent)} 쪽은 이번에 준비하지 못했어요.</p>
          <Button
            variant="tertiary"
            size="compact"
            className="mt-3"
            onClick={() => suggestions.refetch()}
          >
            다시 시도
          </Button>
        </CardFailed>
      ))}

      {/* 🚨 일반 추천이 먼저, 되묻는 질문이 그 뒤다. 부모가 이 화면에 온 이유는 "지금 뭘 할까" 고,
          질문은 "다음엔 더 맞추기" 다 — 순서를 뒤집으면 답부터 요구하는 화면이 된다.
          카드마다 "또래 기준" 라벨과 기록 건수가 붙으므로 위에 따로 머리글을 두지 않는다. */}
      {scarcity && general.length > 0 ? (
        <ul className="flex flex-col gap-3">
          {general.map((item) => (
            <GeneralSuggestionCard key={item.id} suggestion={item} recordCount={scarcity.count} />
          ))}
        </ul>
      ) : null}

      {scarcity ? (
        <ScarcityCard childId={childId} scarcity={scarcity} hasGeneral={general.length > 0} />
      ) : null}

      {/* 🚨 "고르면 저장될 내용을 먼저 보여드려요" 를 여기 두지 않는다 — 바로 위 요약 카드가
          이미 같은 말을 한다. 한 화면에서 두 번 하면 둘 다 흘려 읽게 된다. */}

      {/* 🚨 화면 바닥에 붙이지 않는다. 후보가 한두 개뿐인 화면에서 `mt-auto` 는 목록과 버튼 사이에
          빈 화면을 한 폭 만든다 — 나가는 버튼은 내용 바로 뒤를 따라간다. */}
      <Button variant="secondary" block onClick={() => router.push(`/child/${childId}/home`)}>
        홈으로
      </Button>

      {approving ? (
        <ApprovalSheet
          open
          onClose={() => {
            setApproving(null);
            createDrafts.reset();
          }}
          childId={childId}
          result={approving}
        />
      ) : null}
    </Screen>
  );
}

/**
 * 근거가 부족할 때 되묻는 자리. 🚨 **질문은 한 개까지만** 드린다 (CLAUDE.md §2).
 * 쌓인 기록 건수를 숨기지 않고 그대로 보여준다 — 없는 근거로 "우리 아이 맞춤" 인 척하지 않는다.
 *
 * 🚨 **위에 일반 추천을 그렸는지에 따라 문구가 달라진다.** 또래 기준 추천을 이미 보여 준 화면에서
 *    "추천을 만들지 않겠다" 고 말하면 화면이 스스로를 부정한다. 카드가 두 경우를 다 알고 있어야
 *    서버가 `general` 을 안 보낼 때도 말이 된다.
 */
function ScarcityCard({
  childId,
  scarcity,
  hasGeneral,
}: {
  childId: string;
  scarcity: Scarcity;
  hasGeneral: boolean;
}) {
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
      <h2 className="text-section text-ink">
        {hasGeneral ? "다음엔 우리 아이 기준으로 골라 드릴게요" : "판단할 기록이 부족해요"}
      </h2>
      <p className="text-body-sm text-ink-muted mt-1">
        {scarcity.count === 0
          ? "이 주제로 쌓인 기록이 아직 없어요."
          : `이 주제로 쌓인 기록이 ${scarcity.count}건뿐이에요.`}{" "}
        {hasGeneral
          ? "그래서 위 추천은 또래 기준이에요. 하나만 알려주시면 다음부터 달라져요."
          : "근거 없이 추천을 만들지는 않을게요."}
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
