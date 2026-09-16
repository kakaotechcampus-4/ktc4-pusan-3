"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Notebook, Sprout, ThumbsUp } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState, type ReactNode } from "react";

import { AffinityList } from "@/components/affinity-list";
import { AuthGate } from "@/components/auth-gate";
import { ChildNav } from "@/components/child-nav";
import { ConsentRequiredCard } from "@/components/consent-required-card";
import { domainLabel } from "@/components/domain-chip";
import { MemoryDetailSheet, type MemoryTarget } from "@/components/memory-detail-sheet";
import { ObservationList } from "@/components/observation-list";
import { SuggestionFeedbackList } from "@/components/suggestion-feedback-list";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { PageTitle } from "@/components/ui/page-title";
import { Screen } from "@/components/ui/screen";
import { Select } from "@/components/ui/select";
import { SkeletonBlock } from "@/components/ui/skeleton";
import { Tabs, type TabItem } from "@/components/ui/tabs";
import { useChildId } from "@/hooks/use-child-id";
import {
  AGENTS,
  api,
  isApiError,
  qk,
  type Affinity,
  type AffinitiesResponse,
  type Agent,
  type Observation,
  type ObservationsResponse,
  type Suggestion,
  type SuggestionFeedback,
  type SuggestionFeedbackResponse,
  type SuggestionListResponse,
} from "@/lib/api";

/**
 * 07 기억 — 관찰 · 프로필 · 제안 피드백.
 *
 * 🚨 **관찰과 프로필은 다른 탭이고 다른 엔드포인트다** (계약서 §08). 한 목록에 섞고 라벨로만
 *    가르면 "한 번 본 것" 과 "확정된 성향" 이 같은 무게로 읽히고, 그러면 **한 번의 관찰을
 *    성향으로 확정하지 않는다**(CLAUDE.md §2)가 화면에서 사라진다. 목록의 모양까지 다르다 —
 *    관찰은 줄, 프로필은 카드다.
 *
 * 🚨 **세 탭이 같은 층위가 아니다.** 관찰·프로필은 기억이고 피드백은 제안이다. 피드백은
 *    제안 가중치만 바꾸고 기억을 건드리지 않는다는 사실을 그 탭이 화면에 쓴다.
 *
 * 🚨 **탭 전환은 URL 에 남긴다** (문서 §7). 셋이 다른 엔드포인트라 뒤로가기가 동작해야 한다.
 *
 * 🚨 **교정에 확인 단계를 두지 않는다.** 승인 게이트는 딱 2곳이고 여기는 그 둘이 아니다.
 */
const TAB_KEYS = ["observations", "profile", "feedback"] as const;
type MemoryTab = (typeof TAB_KEYS)[number];

function isTab(value: string | null): value is MemoryTab {
  return value !== null && (TAB_KEYS as readonly string[]).includes(value);
}

function isAgent(value: string | null): value is Agent {
  return value !== null && (AGENTS as readonly string[]).includes(value);
}

/* ── 필터 ─────────────────────────────────────────────────────────────── */

/**
 * 🚨 **정렬을 만들지 않는다.** 두 엔드포인트 다 계약서에 정렬 파라미터가 없다 —
 *    관찰은 `observed_to DESC` 고정이고(§08) 프로필은 `?domain=&state=` 뿐이다. 커서
 *    페이지네이션이라 받아 온 쪽만 다시 정렬하면 다음 장을 붙이는 순간 순서가 어긋난다.
 *    정렬이 필요하면 계약서 먼저다 (최상위 §8 영역 간 인터페이스).
 */
const FILTER_STATES = ["all", "confirmed", "candidate"] as const;
type AffinityFilterState = (typeof FILTER_STATES)[number];

function isFilterState(value: string | null): value is AffinityFilterState {
  return value !== null && (FILTER_STATES as readonly string[]).includes(value);
}

const DOMAIN_OPTIONS = [
  { value: "all", label: "전체" },
  ...AGENTS.map((agent) => ({ value: agent, label: domainLabel(agent) })),
] as const;

/** 🚨 기억에는 health 가 없다 — `Affinity.domain` 이 health 를 뺀 3종이다 (승격 파이프라인 밖). */
const AFFINITY_DOMAIN_OPTIONS = [
  { value: "all", label: "전체" },
  ...AGENTS.filter((agent) => agent !== "health").map((agent) => ({
    value: agent,
    label: domainLabel(agent),
  })),
] as const;

const USE_OPTIONS = [
  { value: "all", label: "전체" },
  { value: "unused", label: "사용되지 않은 기록만" },
] as const;

/**
 * 🚨 **`archived` 를 고르게 두지 않는다.** 교정으로 내려간 기억을 다시 꺼내 보는 길인데,
 *    이 제품은 교정을 되돌리는 기능을 주지 않기로 했다. 목록에 없는 것을 필터로만 되살리면
 *    "되돌릴 수 있다" 는 기대를 만들면서 되돌릴 수단은 안 주는 셈이다.
 */
const STATE_OPTIONS = [
  { value: "all", label: "전체" },
  { value: "confirmed", label: "확인됨" },
  { value: "candidate", label: "후보" },
] as const;

/** 좁은 폰에서 둘이 나란히 서고, 더 좁아지면 줄바꿈한다. */
function FilterRow({ children }: { children: ReactNode }) {
  return <div className="flex flex-wrap items-end gap-2">{children}</div>;
}

export default function MemoriesPage() {
  return (
    // 🚨 `useSearchParams()` 는 Suspense 경계 안에 있어야 한다 (05 화면과 같은 이유).
    <Suspense fallback={null}>
      <AuthGate>
        <MemoriesScreen />
      </AuthGate>
    </Suspense>
  );
}

function MemoriesScreen() {
  const childId = useChildId();
  const router = useRouter();
  const searchParams = useSearchParams();

  const tabParam = searchParams.get("tab");
  const tab: MemoryTab = isTab(tabParam) ? tabParam : "observations";
  const domainParam = searchParams.get("domain");
  const domain: Agent | null = isAgent(domainParam) ? domainParam : null;
  const unusedOnly = searchParams.get("unused") === "1";
  /**
   * 기억 탭의 승격 상태 필터. 🚨 **`archived` 는 고르게 두지 않는다** — 교정으로 내려간 기억을
   * 다시 꺼내 보는 길이고, 이 제품은 교정을 되돌리는 기능을 주지 않기로 했다. 목록에 없는
   * 것을 필터로만 되살리면 "되돌릴 수 있다" 는 기대를 만들면서 되돌릴 수단은 안 주는 셈이다.
   */
  const stateParam = searchParams.get("state");
  const state: AffinityFilterState = isFilterState(stateParam) ? stateParam : "all";

  const [target, setTarget] = useState<MemoryTarget | null>(null);

  const base = `/child/${childId}/memories`;
  const tabs: TabItem[] = [
    { key: "observations", label: "기록", href: base },
    { key: "profile", label: "기억", href: `${base}?tab=profile` },
    { key: "feedback", label: "제안 피드백", href: `${base}?tab=feedback` },
  ];

  /**
   * 필터를 주소에 남긴다 — 뒤로가기가 동작해야 하고, 링크로 그 상태를 그대로 열 수 있어야 한다.
   * 🚨 **탭을 옮기면 필터는 따라가지 않는다.** 두 목록이 서로 다른 것이라 `?domain=` 이 같은
   *    뜻이어도 고른 자리가 다르다 — 탭 링크는 필터 없는 주소를 가리킨다.
   */
  function setFilter(next: {
    domain?: Agent | null;
    unused?: boolean;
    state?: AffinityFilterState;
  }) {
    const params = new URLSearchParams();
    if (tab !== "observations") params.set("tab", tab);

    const nextDomain = next.domain === undefined ? domain : next.domain;
    const nextUnused = next.unused === undefined ? unusedOnly : next.unused;
    const nextState = next.state === undefined ? state : next.state;

    if (nextDomain) params.set("domain", nextDomain);
    if (tab === "observations" && nextUnused) params.set("unused", "1");
    if (tab === "profile" && nextState !== "all") params.set("state", nextState);

    const query = params.toString();
    router.replace(query ? `${base}?${query}` : base, { scroll: false });
  }

  return (
    <Screen className="gap-5" nav={<ChildNav active="memories" />}>
      <header>
        <PageTitle>기록과 기억</PageTitle>
        <p className="text-body-sm text-ink-muted mt-2">
          적어주신 말이 기록으로 쌓이고, 같은 것이 반복되면 기억이 돼요.
        </p>
      </header>

      <Tabs items={tabs} active={tab} label="기억 보기" />

      {tab === "observations" ? (
        <ObservationsTab
          childId={childId}
          domain={domain}
          unusedOnly={unusedOnly}
          onFilter={setFilter}
          onOpen={(observation) => setTarget({ type: "observation", observation })}
        />
      ) : null}

      {tab === "profile" ? (
        <ProfileTab
          domain={domain}
          state={state}
          onFilter={setFilter}
          childId={childId}
          onOpen={(affinity) => setTarget({ type: "affinity", affinity })}
        />
      ) : null}

      {tab === "feedback" ? <FeedbackTab childId={childId} /> : null}

      <MemoryDetailSheet childId={childId} target={target} onClose={() => setTarget(null)} />
    </Screen>
  );
}

/* ── 관찰 ─────────────────────────────────────────────────────────────── */

function ObservationsTab({
  childId,
  domain,
  unusedOnly,
  onFilter,
  onOpen,
}: {
  childId: string;
  domain: Agent | null;
  unusedOnly: boolean;
  onFilter: (next: { domain?: Agent | null; unused?: boolean }) => void;
  onOpen: (observation: Observation) => void;
}) {
  const router = useRouter();
  const filters = { domain, unusedOnly };

  const observations = useQuery({
    queryKey: qk.observations(childId, filters),
    queryFn: () =>
      api.get<ObservationsResponse>(`/children/${childId}/observations`, {
        query: {
          ...(domain ? { domain } : {}),
          ...(unusedOnly ? { unused_in_suggestions: "true" } : {}),
        },
      }),
  });

  const filtered = domain !== null || unusedOnly;

  return (
    <section className="flex flex-col gap-3">
      {/* 🚨 **정렬은 두지 않는다.** 계약서가 이 목록을 `observed_to DESC` 로 고정했고(§08)
          정렬 파라미터가 없다. 커서 페이지네이션이라 받아 온 쪽만 프론트에서 다시 정렬하면
          다음 장을 붙이는 순간 순서가 어긋난다 — 서버가 주는 순서를 그대로 쓴다. */}
      <FilterRow>
        <Select
          label="분류"
          value={domain ?? "all"}
          options={DOMAIN_OPTIONS}
          onChange={(value) => onFilter({ domain: value === "all" ? null : value })}
        />
        <Select
          label="보기"
          value={unusedOnly ? "unused" : "all"}
          options={USE_OPTIONS}
          onChange={(value) => onFilter({ unused: value === "unused" })}
        />
      </FilterRow>

      {observations.isPending ? <SkeletonBlock label="기록을 불러오는 중" /> : null}

      {observations.isError ? (
        <LoadFailed what="기록을" childId={childId} error={observations.error} />
      ) : null}

      {observations.data ? (
        observations.data.items.length === 0 ? (
          filtered ? (
            <EmptyState
              icon={Notebook}
              title="이 조건에 맞는 기록이 없어요"
              description={
                unusedOnly
                  ? "적어주신 기록이 전부 제안에 쓰이고 있어요. 다른 분류를 골라보세요."
                  : "다른 분류를 골라보세요."
              }
              count={0}
              countLabel="이 조건의 기록"
            />
          ) : (
            <EmptyState
              icon={Notebook}
              title="아직 적은 말이 없어요"
              description="홈에서 오늘 있었던 일을 한 줄 남기면 여기에 쌓여요."
              count={0}
              action={
                <Button
                  variant="secondary"
                  block
                  onClick={() => router.push(`/child/${childId}/home`)}
                >
                  홈에서 한 줄 적기
                </Button>
              }
            />
          )
        ) : (
          <>
            {/* 🚨 건수를 숨기지 않는다 (문서 §7). 필터를 걸면 그 필터의 건수다. */}
            <p className="text-caption text-ink-subtle">기록 {observations.data.total}건</p>
            <ObservationList observations={observations.data.items} onOpen={onOpen} />
          </>
        )
      ) : null}
    </section>
  );
}

/* ── 프로필 ───────────────────────────────────────────────────────────── */

function ProfileTab({
  childId,
  domain,
  state,
  onFilter,
  onOpen,
}: {
  childId: string;
  domain: Agent | null;
  state: AffinityFilterState;
  onFilter: (next: { domain?: Agent | null; state?: AffinityFilterState }) => void;
  onOpen: (affinity: Affinity) => void;
}) {
  const filters = { domain, state };

  const affinities = useQuery({
    queryKey: qk.affinities(childId, filters),
    queryFn: () =>
      api.get<AffinitiesResponse>(`/children/${childId}/affinities`, {
        query: {
          ...(domain ? { domain } : {}),
          ...(state === "all" ? {} : { state }),
        },
      }),
  });

  const filtered = domain !== null || state !== "all";

  return (
    <section className="flex flex-col gap-3">
      {/* 🚨 여기도 정렬을 두지 않는다 — 계약서의 이 엔드포인트는 `?domain=&state=` 뿐이다. */}
      <FilterRow>
        <Select
          label="분류"
          value={domain ?? "all"}
          options={AFFINITY_DOMAIN_OPTIONS}
          onChange={(value) => onFilter({ domain: value === "all" ? null : value })}
        />
        <Select
          label="상태"
          value={state}
          options={STATE_OPTIONS}
          onChange={(value) => onFilter({ state: value })}
        />
      </FilterRow>

      {affinities.isPending ? <SkeletonBlock label="기억을 불러오는 중" /> : null}

      {affinities.isError ? (
        <LoadFailed what="기억을" childId={childId} error={affinities.error} />
      ) : null}

      {affinities.data ? (
        affinities.data.affinities.length === 0 ? (
          filtered ? (
            <EmptyState
              icon={Sprout}
              title="이 조건에 맞는 기억이 없어요"
              description="다른 분류나 상태를 골라보세요."
              count={0}
              countLabel="이 조건의 기억"
            />
          ) : (
            <EmptyState
              icon={Sprout}
              title="아직 확정된 기억이 없어요"
              description="같은 것이 서로 다른 날에 반복되면 기억이 돼요. 한 번 본 것은 성향으로 확정하지 않아요."
              count={0}
              countLabel="쌓인 기억"
            />
          )
        ) : (
          <>
            <p className="text-caption text-ink-subtle">
              기억 {affinities.data.affinities.length}건
            </p>
            <AffinityList affinities={affinities.data.affinities} onOpen={onOpen} />
          </>
        )
      ) : null}
    </section>
  );
}

/* ── 제안 피드백 ─────────────────────────────────────────────────────── */

function FeedbackTab({ childId }: { childId: string }) {
  const queryClient = useQueryClient();
  const [pendingId, setPendingId] = useState<string | null>(null);

  /**
   * ⚠️ `GET /children/{cid}/suggestions` 는 **계약서 v1 에 아직 없다** (types.ts 의 ⚠️).
   *    계약서 §03 이 07 에 `PATCH /feedback` 을 배정했는데 평가할 대상을 목록으로 얻을 길이
   *    없어서 제안해 두고 목으로 먼저 세웠다. 서버가 없으면 빈 상태가 그려진다.
   */
  const received = useQuery({
    queryKey: qk.suggestionList(childId),
    queryFn: () => api.get<SuggestionListResponse>(`/children/${childId}/suggestions`),
  });

  const sendFeedback = useMutation({
    mutationFn: ({
      suggestion,
      feedback,
    }: {
      suggestion: Suggestion;
      feedback: SuggestionFeedback;
    }) =>
      api.patch<SuggestionFeedbackResponse>(`/suggestions/${suggestion.id}/feedback`, { feedback }),
    onMutate: ({ suggestion }) => setPendingId(suggestion.id),
    onSettled: () => setPendingId(null),
    onSuccess: () => {
      // 🚨 기억은 안 바뀐다(`memory_changed: false`). 제안 목록만 다시 읽는다 —
      //    아이 스코프를 통째로 무효화하면 관찰·프로필까지 다시 부르게 되고,
      //    그건 이 동작이 실제로 하는 일보다 넓다.
      void queryClient.invalidateQueries({ queryKey: qk.suggestionList(childId) });
    },
  });

  return (
    <section className="flex flex-col gap-3">
      {/* 🚨 계약서 §08 의 `memory_changed: false` 와 같은 말이다. 피드백과 교정을 헷갈리면
          부모가 "안 좋아했어요" 를 눌러 기억이 지워진 줄 안다. */}
      <p className="text-body-sm text-ink-muted">
        피드백은 다음 제안의 무게만 바꿔요. 기억은 그대로 둬요. 기억을 고치려면 관찰이나 프로필
        탭에서 고쳐주세요.
      </p>

      {received.isPending ? <SkeletonBlock label="받은 제안을 불러오는 중" /> : null}

      {received.isError ? (
        <LoadFailed what="받은 제안을" childId={childId} error={received.error} />
      ) : null}

      {received.data ? (
        received.data.items.length === 0 ? (
          <EmptyState
            icon={ThumbsUp}
            title="아직 받은 제안이 없어요"
            description="제안을 한 번 받으면 여기서 어땠는지 알려줄 수 있어요."
            count={0}
            countLabel="받은 제안"
          />
        ) : (
          <SuggestionFeedbackList
            suggestions={received.data.items}
            pendingId={pendingId}
            onSelect={(suggestion, feedback) => sendFeedback.mutate({ suggestion, feedback })}
          />
        )
      ) : null}

      {sendFeedback.isError ? (
        <p className="text-body-sm text-ink-muted" role="status">
          지금은 보내지 못했어요. 잠시 뒤에 다시 눌러주세요.
        </p>
      ) : null}
    </section>
  );
}

/* ── 실패 ─────────────────────────────────────────────────────────────── */

/**
 * 🚨 **실패를 빨강으로 칠하지 않는다** (문서 §3). 🚨 **기본값으로 대체하지 않는다** —
 *    못 불러왔으면 못 불러왔다고 화면에 말한다 (apps/web/CLAUDE.md §3).
 */
function LoadFailed({ what, childId, error }: { what: string; childId: string; error: unknown }) {
  if (isApiError(error) && error.code === "consent_required") {
    return <ConsentRequiredCard childId={childId} error={error} what={`${what} 볼 수 없어요.`} />;
  }

  return (
    <div className="bg-surface-muted rounded-card text-body-sm text-ink-muted p-4" role="status">
      {what} 지금 불러오지 못했어요. 잠시 뒤에 다시 열어주세요.
    </div>
  );
}
