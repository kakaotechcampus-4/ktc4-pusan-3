"use client";

import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Notebook, Search, SlidersHorizontal, Sprout, ThumbsUp, X } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef, useState, type FormEvent } from "react";

import { AffinityList, AffinityListSkeleton } from "@/components/affinity-list";
import { AuthGate } from "@/components/auth-gate";
import { ChildNav } from "@/components/child-nav";
import { ConsentRequiredCard } from "@/components/consent-required-card";
import { CORRECTED_OBSERVATION } from "@/components/correction-buttons";
import { domainLabel } from "@/components/domain-chip";
import { MemoryDetailSheet, type MemoryTarget } from "@/components/memory-detail-sheet";
import { ObservationList, ObservationListSkeleton } from "@/components/observation-list";
import {
  SuggestionFeedbackList,
  SuggestionFeedbackListSkeleton,
} from "@/components/suggestion-feedback-list";
import { Button } from "@/components/ui/button";
import { BottomSheet } from "@/components/ui/bottom-sheet";
import { ChipRow, RemovableChip } from "@/components/ui/chip";
import { DateRangeField } from "@/components/ui/date-field";
import { EmptyState } from "@/components/ui/empty-state";
import { ICON_SIZE, ICON_STROKE } from "@/components/ui/icon";
import { IconButton } from "@/components/ui/icon-button";
import { PageTitle } from "@/components/ui/page-title";
import { Pagination } from "@/components/ui/pagination";
import { Screen } from "@/components/ui/screen";
import { Select } from "@/components/ui/select";
import { SkeletonGroup, SkeletonText } from "@/components/ui/skeleton";
import { phaseOf, Swap } from "@/components/ui/swap";
import { Tabs, type TabItem } from "@/components/ui/tabs";
import { TextInput } from "@/components/ui/text-input";
import { useChildId } from "@/hooks/use-child-id";
import {
  AGENTS,
  api,
  isApiError,
  qk,
  type Affinity,
  type AffinitiesResponse,
  type Agent,
  type ListQuery,
  type NumberedPage,
  type Observation,
  type ObservationsResponse,
  type ReceivedSuggestion,
  type SuggestionFeedback,
  type SuggestionFeedbackRequest,
  type SuggestionFeedbackResponse,
  type SuggestionListResponse,
} from "@/lib/api";
import { EARLIEST_BIRTH_DATE } from "@/lib/date-bounds";
import { formatDateRange, parseISODate, toISODate } from "@/lib/format";

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

/**
 * 두 목록이 받는 분류. 🚨 **health 가 없다** — 이유가 탭마다 다르다.
 * - 기억: `Affinity.domain` 이 health 를 뺀 3종이다 (승격 파이프라인 밖).
 * - 기록: 건강 관찰은 첫 배포 범위 밖이라 서버가 늘 빈 목록을 준다 (#259). 그대로 두면
 *   "이 조건에 맞는 기록이 없어요" 가 떠서, 건강 이야기를 남긴 보호자에게 사실과 다른 말이 된다 (#269).
 *
 * 주소에 `?domain=health` 가 남아 있어도 "전체" 로 읽는다 — 고를 수 없는 값이 고른 채로 서지 않게.
 */
type ListDomain = Exclude<Agent, "health">;

function isListDomain(value: string | null): value is ListDomain {
  return value !== null && value !== "health" && (AGENTS as readonly string[]).includes(value);
}

/* ── 필터 ─────────────────────────────────────────────────────────────── */

/**
 * 🚨 **정렬을 만들지 않는다.** 두 엔드포인트 다 계약서에 정렬 파라미터가 없다 —
 *    관찰은 `observed_to DESC`, 기억은 `last_observed_on DESC` 고정이다(#584). 번호 쪽으로
 *    나눠 받으므로 받은 쪽만 다시 정렬하면 쪽 사이의 순서가 어긋난다.
 *    정렬이 필요하면 계약서 먼저다 (최상위 §8 영역 간 인터페이스).
 */
const FILTER_STATES = ["all", "confirmed", "candidate"] as const;
type AffinityFilterState = (typeof FILTER_STATES)[number];

function isFilterState(value: string | null): value is AffinityFilterState {
  return value !== null && (FILTER_STATES as readonly string[]).includes(value);
}

/** 🚨 health 가 없다 — `isListDomain` 의 이유. 기록과 기억이 같은 선택지를 쓴다. */
const DOMAIN_OPTIONS = [
  { value: "all", label: "전체" },
  ...AGENTS.filter((agent): agent is ListDomain => agent !== "health").map((agent) => ({
    value: agent,
    label: domainLabel(agent),
  })),
] as const;

/**
 * 기록 탭의 상태 필터 — 서버 `?status=` 하나를 고른다 (#266). 주지 않으면 세 상태 모두다.
 *
 * ⚠️ **"사용되지 않은 기록만"(`unused_in_suggestions`)은 없다.** 이 자리가 상태 필터로 바뀌었고
 *    (#266), 쓰는 곳이 없어 서버에서도 파라미터를 지웠다 (#286).
 * 🚨 이름은 고치기 버튼과 **같은 말**이다 (`CORRECTED_OBSERVATION`) — 누른 버튼 이름으로 찾게.
 */
const OBSERVATION_STATUS_FILTERS = ["all", "active", "stand_alone", "inactive"] as const;
type ObservationStatusFilter = (typeof OBSERVATION_STATUS_FILTERS)[number];

function isObservationStatusFilter(value: string | null): value is ObservationStatusFilter {
  return value !== null && (OBSERVATION_STATUS_FILTERS as readonly string[]).includes(value);
}

const OBSERVATION_STATUS_OPTIONS = [
  { value: "all", label: "전체" },
  { value: "active", label: "고치지 않은 기록" },
  { value: "stand_alone", label: CORRECTED_OBSERVATION.stand_alone.label },
  { value: "inactive", label: CORRECTED_OBSERVATION.inactive.label },
] as const;

/**
 * ⚠️ **`archived` 는 고르게 두지 않는다.** 처음 이유는 "고치기로 내려간 기억을 필터로만 되살리지
 *    않는다" 였는데, 서버(#277)에서 `archived` 는 고치기가 아니라 기록이 오래 없을 때만 생긴다.
 *    그 이유는 사라졌고, 다시 열지는 따로 정한다 (apps/web/CLAUDE.md §3).
 */
const STATE_OPTIONS = [
  { value: "all", label: "전체" },
  { value: "confirmed", label: "확인됨" },
  { value: "candidate", label: "후보" },
] as const;

/**
 * 기록과 기억 목록이 같이 받는 검색어 · 기간 · 쪽 (#584). 필터처럼 주소에 남는다.
 *
 * 🚨 **주소의 값을 그대로 믿지 않는다.** 서버는 100자 넘는 검색어, 없는 날짜, 시작이 끝보다 늦은
 *    기간, 10,000 을 넘는 쪽을 400 으로 돌려준다 (`api/v1/list_query.py`). 손으로 고친 주소가
 *    실패 화면이 되지 않게, 그런 값은 없는 것으로 읽는다 (`?domain=health` 를 전체로 읽는 것과 같다).
 */
interface ListParams {
  q: string | null;
  from: string | null;
  to: string | null;
  /** 1부터. */
  page: number;
}

/** 서버 `list_query.py` 의 상한 둘. */
const MAX_PAGE = 10_000;
const MAX_QUERY_LENGTH = 100;

function readQuery(value: string | null): string | null {
  const trimmed = value?.trim() ?? "";
  return trimmed !== "" && trimmed.length <= MAX_QUERY_LENGTH ? trimmed : null;
}

/** `YYYY-MM-DD` 이고 달력에 있는 날만. "2026-02-31" 은 3월로 넘어가서 되돌리면 글자가 달라진다. */
function readDate(value: string | null): string | null {
  const date = value === null ? null : parseISODate(value);
  return date !== null && toISODate(date) === value ? value : null;
}

function readPage(value: string | null): number {
  const page = Number(value);
  return Number.isInteger(page) && page >= 1 && page <= MAX_PAGE ? page : 1;
}

function readListParams(params: { get: (name: string) => string | null }): ListParams {
  const from = readDate(params.get("from"));
  const to = readDate(params.get("to"));
  // 🚨 기간은 두 끝이 함께 있어야 한다 — 기간 칸은 한쪽만 열린 기간을 만들지 않는다(한 날이면 그날 하루).
  //    한쪽만 있거나 거꾸로 된 기간은 둘 다 버린다 — 어느 쪽이 잘못 들어온 값인지 화면이 알 수 없다.
  const valid = from !== null && to !== null && from <= to;
  return {
    q: readQuery(params.get("q")),
    from: valid ? from : null,
    to: valid ? to : null,
    page: readPage(params.get("page")),
  };
}

/** 서버로 보내는 모양. 없는 값은 싣지 않는다 (`buildUrl` 이 `undefined` 를 뺀다). */
function toListQuery(list: ListParams): ListQuery {
  return {
    q: list.q ?? undefined,
    date_from: list.from ?? undefined,
    date_to: list.to ?? undefined,
    page: list.page,
  };
}

/** 한 번에 바꾸는 것. 주지 않은 칸은 지금 값 그대로다 — 쪽만 예외다 (`setFilter`). */
interface FilterChange {
  domain?: ListDomain | null;
  status?: ObservationStatusFilter;
  state?: AffinityFilterState;
  q?: string | null;
  from?: string | null;
  to?: string | null;
  page?: number;
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
  const domain: ListDomain | null = isListDomain(domainParam) ? domainParam : null;
  const statusParam = searchParams.get("status");
  const status: ObservationStatusFilter = isObservationStatusFilter(statusParam)
    ? statusParam
    : "all";
  /**
   * 기억 탭의 승격 상태 필터. `archived` 는 없다 (위 `STATE_OPTIONS` 의 ⚠️).
   */
  const stateParam = searchParams.get("state");
  const state: AffinityFilterState = isFilterState(stateParam) ? stateParam : "all";
  const list = readListParams(searchParams);

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
   *    뜻이어도 고른 자리가 다르다 — 탭 링크는 필터 없는 주소를 가리킨다. 검색어 · 기간 · 쪽도 같다.
   * 🚨 **쪽 말고 다른 것을 바꾸면 1쪽으로 돌아간다.** 3쪽을 보다가 분류를 바꾸면 그 분류의 3쪽이
   *    아니라 처음부터다 — 조건이 바뀌면 쪽 번호는 다른 목록의 번호가 된다.
   */
  function setFilter(next: FilterChange) {
    const params = new URLSearchParams();
    if (tab !== "observations") params.set("tab", tab);

    const nextDomain = next.domain === undefined ? domain : next.domain;
    const nextStatus = next.status === undefined ? status : next.status;
    const nextState = next.state === undefined ? state : next.state;
    const nextQ = next.q === undefined ? list.q : next.q;
    const nextFrom = next.from === undefined ? list.from : next.from;
    const nextTo = next.to === undefined ? list.to : next.to;
    const nextPage = next.page ?? 1;

    if (nextDomain) params.set("domain", nextDomain);
    if (tab === "observations" && nextStatus !== "all") params.set("status", nextStatus);
    if (tab === "profile" && nextState !== "all") params.set("state", nextState);
    if (nextQ) params.set("q", nextQ);
    if (nextFrom) params.set("from", nextFrom);
    if (nextTo) params.set("to", nextTo);
    if (nextPage > 1) params.set("page", String(nextPage));

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

      {/* 탭을 바꾸면 그 자리의 내용이 갈아 끼워진다 (디자인 시스템 §8 "교체"). 탭 표식은 색으로만 바뀐다. */}
      <Swap id={tab}>
        {tab === "observations" ? (
          <ObservationsTab
            childId={childId}
            domain={domain}
            status={status}
            list={list}
            onFilter={setFilter}
            onOpen={(observation) => setTarget({ type: "observation", observation })}
          />
        ) : null}

        {tab === "profile" ? (
          <ProfileTab
            domain={domain}
            state={state}
            list={list}
            onFilter={setFilter}
            childId={childId}
            onOpen={(affinity) => setTarget({ type: "affinity", affinity })}
          />
        ) : null}

        {tab === "feedback" ? <FeedbackTab childId={childId} /> : null}
      </Swap>

      <MemoryDetailSheet childId={childId} target={target} onClose={() => setTarget(null)} />
    </Screen>
  );
}

/* ── 검색 · 필터 · 쪽 ─────────────────────────────────────────────────── */

/**
 * 검색칸 (#584). 🚨 **치는 동안 요청하지 않는다.** 보내기(키보드의 검색 · Enter)로만 찾는다 —
 *    한글은 조합 중인 글자가 값에 섞여 들어와서, 칠 때마다 부르면 "ㄱ" · "계" · "곙" 이 다 요청이 된다.
 * ⚠️ 예외는 칸을 비웠을 때다. 지우기(×)나 지우개로 비우면 바로 전체로 돌아간다 — 빈 칸 아래에
 *    검색 결과가 남아 있으면 무엇으로 걸러진 목록인지 화면에서 사라진다.
 *
 * 🚨 **칸을 다시 마운트하지 않는다** (`key` 로 갈아 끼우지 않는다). 비우는 순간 주소가 바뀌는데,
 *    그때 칸이 새로 서면 포커스가 빠져서 모바일 키보드가 닫히고 이어 친 글자가 사라진다.
 *    대신 **조건이 바뀔 때마다**(`syncKey`) 칸의 글자를 주소의 검색어로 되돌린다 — 보내지 않은 말이
 *    칸에 남은 채 분류만 바뀌면, 칸에 보이는 말로 걸러진 목록처럼 읽힌다.
 * 🚨 **이 칸이 보낸 변경이 돌아온 것은 되돌리지 않는다** (`sent`). 주소는 서버 왕복 뒤에 바뀌어서,
 *    비우고 바로 다시 친 글자가 그 사이에 들어온다 — 되돌리면 친 글자가 지워지고 한글 조합이 깨진다.
 * 🚨 **지우기는 우리 아이콘 버튼이다.** `type="search"` 에 브라우저가 그려 넣는 ×는 숨긴다 (`TextInput`).
 * ⚠️ 라벨은 `sr-only` 다 — 칸이 하는 일은 앞의 돋보기와 placeholder 가 보여준다 (`TextInput` 의 `labelHidden`).
 */
function SearchField({
  value,
  syncKey,
  placeholder,
  onSearch,
}: {
  value: string | null;
  /** 목록 조건 전체. 바뀌면 칸을 `value` 로 맞춘다. */
  syncKey: string;
  placeholder: string;
  onSearch: (q: string | null) => void;
}) {
  const input = useRef<HTMLInputElement>(null);
  const [draft, setDraft] = useState(value ?? "");
  const [syncedKey, setSyncedKey] = useState(syncKey);
  /** 이 칸이 보내고 아직 주소에 닿지 않은 검색어. `undefined` 는 "보낸 것 없음" 이다. */
  const [sent, setSent] = useState<string | null | undefined>(undefined);
  // 렌더 중에 맞춘다 — effect 로 맞추면 한 번은 옛 글자로 그려진다 (React "props 가 바뀔 때 상태 맞추기").
  if (syncKey !== syncedKey) {
    setSyncedKey(syncKey);
    setSent(undefined);
    if (sent === undefined || value !== sent) setDraft(value ?? "");
  }

  /** 지금 주소와 같은 값은 보내지 않는다 — 주소가 안 바뀌면 `sent` 가 남아 다음 변경을 잘못 넘긴다. */
  function send(q: string | null) {
    if (q === value) return;
    setSent(q);
    onSearch(q);
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    send(draft.trim() || null);
  }

  return (
    <form role="search" onSubmit={submit} className="min-w-0 flex-1">
      <TextInput
        ref={input}
        type="search"
        label="찾기"
        labelHidden
        placeholder={placeholder}
        value={draft}
        maxLength={MAX_QUERY_LENGTH}
        enterKeyHint="search"
        autoComplete="off"
        leading={<Search aria-hidden size={ICON_SIZE.md} strokeWidth={ICON_STROKE} />}
        trailing={
          draft !== "" ? (
            <IconButton
              label="검색어 지우기"
              onClick={() => {
                setDraft("");
                send(null);
                // 버튼이 사라지면서 포커스가 `<body>` 로 떨어지지 않게 칸으로 돌려준다.
                input.current?.focus();
              }}
            >
              <X aria-hidden size={ICON_SIZE.md} strokeWidth={ICON_STROKE} />
            </IconButton>
          ) : null
        }
        onChange={(event) => {
          const next = event.currentTarget.value;
          setDraft(next);
          if (next === "") send(null);
        }}
      />
    </form>
  );
}

/** 필터 시트가 고르는 것. 두 탭이 같은 모양을 쓰고, 탭에 없는 칸(`status` · `state`)은 무시된다. */
interface FilterDraft {
  domain: ListDomain | null;
  status: ObservationStatusFilter;
  state: AffinityFilterState;
  from: string | null;
  to: string | null;
}

const EMPTY_FILTER: FilterDraft = {
  domain: null,
  status: "all",
  state: "all",
  from: null,
  to: null,
};

/**
 * 필터 단추 + 시트 (#584). 🚨 **분류 · 상태 · 기간은 시트 안에 둔다** — 목록 위에 다 펼치면 검색칸까지
 *    네 줄이 목록을 화면 아래로 민다. 목록 위에는 걸린 조건만 칩으로 남긴다 (`ConditionChips`).
 *
 * 🚨 **고른 것은 "적용하기" 를 눌러야 걸린다.** 고를 때마다 걸면 칸 하나 바꿀 때마다 시트 뒤의 목록이
 *    다시 받아지고 쪽이 1로 돌아간다 — 한 번에 모아서 건다. 닫으면 고르던 것은 버린다.
 * ⚠️ "초기화" 는 시트 안의 고른 값만 비운다. 적용하기 전까지 목록은 그대로다.
 * 🚨 승인 게이트가 아니다 — `btn-approve` · `caution` 을 쓰지 않는다 (최상위 §2).
 */
function FilterSheet({
  tab,
  value,
  activeCount,
  onApply,
}: {
  tab: "observations" | "profile";
  value: FilterDraft;
  activeCount: number;
  onApply: (next: FilterDraft) => void;
}) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState(value);
  const today = new Date();

  return (
    <>
      {/* 높이는 검색칸에 맞춘다(`self-stretch`) — 버튼 48 · 입력 52 라 그냥 두면 줄이 어긋난다. */}
      <Button
        variant="secondary"
        className="shrink-0 self-stretch"
        aria-haspopup="dialog"
        onClick={() => {
          setDraft(value);
          setOpen(true);
        }}
      >
        <span className="flex items-center gap-1.5">
          <SlidersHorizontal aria-hidden size={ICON_SIZE.md} strokeWidth={ICON_STROKE} />
          필터
          {activeCount > 0 ? (
            <span>
              {activeCount}
              <span className="sr-only">개 걸림</span>
            </span>
          ) : null}
        </span>
      </Button>

      <BottomSheet
        open={open}
        onClose={() => setOpen(false)}
        title={tab === "observations" ? "기록 필터" : "기억 필터"}
        footer={
          <div className="flex gap-2">
            <Button variant="secondary" className="flex-1" onClick={() => setDraft(EMPTY_FILTER)}>
              초기화
            </Button>
            <Button
              className="flex-1"
              onClick={() => {
                onApply(draft);
                setOpen(false);
              }}
            >
              적용하기
            </Button>
          </div>
        }
      >
        <div className="flex flex-col gap-4">
          <Select
            label="분류"
            shape="field"
            value={draft.domain ?? "all"}
            options={DOMAIN_OPTIONS}
            onChange={(next) => setDraft({ ...draft, domain: next === "all" ? null : next })}
          />
          {tab === "observations" ? (
            <Select
              label="상태"
              shape="field"
              value={draft.status}
              options={OBSERVATION_STATUS_OPTIONS}
              onChange={(next) => setDraft({ ...draft, status: next })}
            />
          ) : (
            <Select
              label="상태"
              shape="field"
              value={draft.state}
              options={STATE_OPTIONS}
              onChange={(next) => setDraft({ ...draft, state: next })}
            />
          )}
          {/* 🚨 기억은 **마지막 기록 날짜**로 거른다 — 기록(기간이 겹치면)과 기준이 달라서 글자로 적는다.
              같은 기간을 골라도 두 탭의 결과가 어긋나 보일 수 있는 자리다. */}
          <DateRangeField
            label="기간"
            hint={tab === "profile" ? "마지막으로 기록된 날로 찾아요." : undefined}
            placeholder="전체 기간"
            from={draft.from ?? ""}
            to={draft.to ?? ""}
            onChange={({ from, to }) => setDraft({ ...draft, from, to })}
            fromDate={EARLIEST_BIRTH_DATE}
            toDate={today}
          />
          {draft.from !== null ? (
            <Button
              variant="tertiary"
              size="compact"
              className="self-start"
              onClick={() => setDraft({ ...draft, from: null, to: null })}
            >
              기간 지우기
            </Button>
          ) : null}
        </div>
      </BottomSheet>
    </>
  );
}

/** 목록 위에 남는 걸린 조건 하나. `clear` 는 그 조건만 푸는 변경이다. */
interface Condition {
  key: string;
  label: string;
  clear: FilterChange;
}

/**
 * 걸린 조건 칩 줄 (#584). 시트를 열지 않아도 **무엇으로 걸러진 목록인지** 보이고, 하나씩 풀 수 있다.
 * 🚨 아무것도 안 걸렸으면 줄째 그리지 않는다 — 빈 줄이 목록을 밀지 않게.
 * ⚠️ 검색어는 칩으로 세우지 않는다 — 검색칸에 이미 보이고, 칸의 지우기가 그것을 푼다.
 */
function ConditionChips({
  conditions,
  onFilter,
}: {
  conditions: Condition[];
  onFilter: (next: FilterChange) => void;
}) {
  if (conditions.length === 0) return null;
  return (
    <ChipRow>
      {conditions.map((condition) => (
        <RemovableChip
          key={condition.key}
          removeLabel={`${condition.label} 조건 풀기`}
          onRemove={() => onFilter(condition.clear)}
        >
          {condition.label}
        </RemovableChip>
      ))}
    </ChipRow>
  );
}

/** 분류와 기간 칩은 두 탭이 같다. 상태 칩만 탭이 붙인다. */
function commonConditions(domain: ListDomain | null, list: ListParams): Condition[] {
  const conditions: Condition[] = [];
  if (domain)
    conditions.push({ key: "domain", label: domainLabel(domain), clear: { domain: null } });
  if (list.from !== null && list.to !== null) {
    conditions.push({
      key: "period",
      label: formatDateRange(list.from, list.to),
      clear: { from: null, to: null },
    });
  }
  return conditions;
}

/**
 * 🚨 **받은 쪽이 마지막 쪽을 넘으면 마지막 쪽으로 옮긴다.** 서버는 범위를 넘은 쪽에 오류가 아니라
 *    빈 목록을 준다 — 고치기로 상태 필터의 건수가 줄거나 주소를 손으로 고치면 그렇게 된다.
 *    그대로 두면 "이 조건에 맞는 기록이 없어요" 가 떠서, 앞 쪽에 있는 것을 없다고 말한다.
 *    옮기는 동안은 빈 목록 대신 받는 중 모양을 세운다 (`outOfRange`).
 */
function useLastPageClamp(
  data: NumberedPage | undefined,
  placeholder: boolean,
  onPage: (page: number) => void,
): boolean {
  const outOfRange = data !== undefined && data.page > Math.max(data.total_pages, 1);
  const last = data === undefined ? 1 : Math.max(data.total_pages, 1);
  // 🚨 `onPage` 는 렌더마다 새 함수다. 의존성에 넣으면 범위를 넘은 동안 렌더마다 주소를 다시 쓴다.
  const onPageRef = useRef(onPage);
  useEffect(() => {
    onPageRef.current = onPage;
  });
  useEffect(() => {
    if (outOfRange && !placeholder) onPageRef.current(last);
  }, [outOfRange, placeholder, last]);
  return outOfRange;
}

/* ── 관찰 ─────────────────────────────────────────────────────────────── */

function ObservationsTab({
  childId,
  domain,
  status,
  list,
  onFilter,
  onOpen,
}: {
  childId: string;
  domain: ListDomain | null;
  status: ObservationStatusFilter;
  list: ListParams;
  onFilter: (next: FilterChange) => void;
  onOpen: (observation: Observation) => void;
}) {
  const router = useRouter();
  const filters = { domain, status, ...list };
  const top = useRef<HTMLParagraphElement>(null);

  /**
   * 🚨 **서버는 한 쪽(20건)씩 준다.** 건수는 그 쪽의 줄 수가 아니라 `total` 이다 (#269).
   * 쪽이나 조건을 바꾸는 동안에는 받아 둔 목록을 그대로 둔다(`keepPreviousData`) — 누를 때마다
   * 목록이 스켈레톤으로 꺼졌다 켜지면 어디를 보고 있었는지 잃는다.
   */
  const observations = useQuery({
    queryKey: qk.observations(childId, filters),
    queryFn: () =>
      api.get<ObservationsResponse>(`/children/${childId}/observations`, {
        query: {
          ...(domain ? { domain } : {}),
          ...(status !== "all" ? { status } : {}),
          ...toListQuery(list),
        },
      }),
    placeholderData: keepPreviousData,
  });
  const outOfRange = useLastPageClamp(observations.data, observations.isPlaceholderData, (page) =>
    onFilter({ page }),
  );

  const conditions = commonConditions(domain, list);
  if (status !== "all") {
    const label = OBSERVATION_STATUS_OPTIONS.find((option) => option.value === status)?.label;
    if (label) conditions.splice(1, 0, { key: "status", label, clear: { status: "all" } });
  }
  const filtered = conditions.length > 0 || list.q !== null;

  return (
    <section className="flex flex-col gap-3">
      {/* 🚨 **정렬은 두지 않는다.** 계약서가 이 목록을 `observed_to DESC` 로 고정했고(§08)
          정렬 파라미터가 없다. 번호 쪽이라 받은 쪽만 프론트에서 다시 정렬하면 쪽 사이의
          순서가 어긋난다 — 서버가 주는 순서를 그대로 쓴다. */}
      <div className="flex items-stretch gap-2">
        <SearchField
          value={list.q}
          syncKey={JSON.stringify(filters)}
          placeholder="적어 주신 말에서 찾기"
          onSearch={(q) => onFilter({ q })}
        />
        <FilterSheet
          tab="observations"
          value={{ ...EMPTY_FILTER, domain, status, from: list.from, to: list.to }}
          activeCount={conditions.length}
          onApply={(next) =>
            onFilter({ domain: next.domain, status: next.status, from: next.from, to: next.to })
          }
        />
      </div>
      <ConditionChips conditions={conditions} onFilter={onFilter} />

      <Swap
        id={phaseOf({ isPending: observations.isPending || outOfRange, data: observations.data })}
      >
        {observations.isPending || outOfRange ? (
          // 🚨 받은 뒤와 같은 모양(건수 줄 + 기록 줄)으로 기다린다 — 세 줄짜리 블록이 카드 목록으로
          //    바뀌면 받은 순간 화면 전체가 다시 그려진 것처럼 보인다.
          <SkeletonGroup label="기록을 불러오는 중" className="flex flex-col gap-3">
            <SkeletonText size="caption" width="1/4" />
            <ObservationListSkeleton />
          </SkeletonGroup>
        ) : null}

        {observations.isError && !observations.data ? (
          <LoadFailed what="기록을" childId={childId} error={observations.error} />
        ) : null}

        {observations.data && !outOfRange ? (
          observations.data.items.length === 0 ? (
            filtered ? (
              <EmptyState
                icon={Notebook}
                title="이 조건에 맞는 기록이 없어요"
                description="찾는 말이나 필터를 바꿔 보세요."
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
            <div className="flex flex-col gap-3" aria-busy={observations.isPlaceholderData}>
              {/* 🚨 건수를 숨기지 않는다 (문서 §7). 필터를 걸면 그 필터의 건수다. */}
              <p ref={top} className="text-caption text-ink-subtle scroll-mt-4">
                기록 {observations.data.total}건
              </p>
              <ObservationList observations={observations.data.items} onOpen={onOpen} />
              <Pagination
                label="기록 쪽 넘기기"
                page={list.page}
                totalPages={observations.data.total_pages}
                onChange={(page) => {
                  onFilter({ page });
                  top.current?.scrollIntoView({ block: "start" });
                }}
              />
            </div>
          )
        ) : null}
      </Swap>
    </section>
  );
}

/* ── 프로필 ───────────────────────────────────────────────────────────── */

function ProfileTab({
  childId,
  domain,
  state,
  list,
  onFilter,
  onOpen,
}: {
  childId: string;
  domain: ListDomain | null;
  state: AffinityFilterState;
  list: ListParams;
  onFilter: (next: FilterChange) => void;
  onOpen: (affinity: Affinity) => void;
}) {
  const filters = { domain, state, ...list };
  const top = useRef<HTMLParagraphElement>(null);

  /** 기록 탭과 같은 이유로 한 쪽씩 받고, 바꾸는 동안 받아 둔 카드를 둔다. */
  const affinities = useQuery({
    queryKey: qk.affinities(childId, filters),
    queryFn: () =>
      api.get<AffinitiesResponse>(`/children/${childId}/affinities`, {
        query: {
          ...(domain ? { domain } : {}),
          ...(state === "all" ? {} : { state }),
          ...toListQuery(list),
        },
      }),
    placeholderData: keepPreviousData,
  });
  const outOfRange = useLastPageClamp(affinities.data, affinities.isPlaceholderData, (page) =>
    onFilter({ page }),
  );

  const conditions = commonConditions(domain, list);
  if (state !== "all") {
    const label = STATE_OPTIONS.find((option) => option.value === state)?.label;
    if (label) conditions.splice(1, 0, { key: "state", label, clear: { state: "all" } });
  }
  const filtered = conditions.length > 0 || list.q !== null;

  return (
    <section className="flex flex-col gap-3">
      {/* 🚨 여기도 정렬을 두지 않는다 — 이 엔드포인트도 `last_observed_on DESC` 고정이다 (#584). */}
      <div className="flex items-stretch gap-2">
        <SearchField
          value={list.q}
          syncKey={JSON.stringify(filters)}
          placeholder="기억 이름으로 찾기"
          onSearch={(q) => onFilter({ q })}
        />
        <FilterSheet
          tab="profile"
          value={{ ...EMPTY_FILTER, domain, state, from: list.from, to: list.to }}
          activeCount={conditions.length}
          onApply={(next) =>
            onFilter({ domain: next.domain, state: next.state, from: next.from, to: next.to })
          }
        />
      </div>
      <ConditionChips conditions={conditions} onFilter={onFilter} />

      <Swap id={phaseOf({ isPending: affinities.isPending || outOfRange, data: affinities.data })}>
        {affinities.isPending || outOfRange ? (
          <SkeletonGroup label="기억을 불러오는 중" className="flex flex-col gap-3">
            <SkeletonText size="caption" width="1/4" />
            <AffinityListSkeleton />
          </SkeletonGroup>
        ) : null}

        {affinities.isError && !affinities.data ? (
          <LoadFailed what="기억을" childId={childId} error={affinities.error} />
        ) : null}

        {affinities.data && !outOfRange ? (
          affinities.data.affinities.length === 0 ? (
            filtered ? (
              <EmptyState
                icon={Sprout}
                title="이 조건에 맞는 기억이 없어요"
                description="찾는 말이나 필터를 바꿔 보세요."
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
            <div className="flex flex-col gap-3" aria-busy={affinities.isPlaceholderData}>
              {/* 🚨 건수는 이 쪽의 카드 수가 아니라 `total` 이다 — 쪽으로 나눈 뒤로는 둘이 다르다. */}
              <p ref={top} className="text-caption text-ink-subtle scroll-mt-4">
                기억 {affinities.data.total}건
              </p>
              <AffinityList affinities={affinities.data.affinities} onOpen={onOpen} />
              <Pagination
                label="기억 쪽 넘기기"
                page={list.page}
                totalPages={affinities.data.total_pages}
                onChange={(page) => {
                  onFilter({ page });
                  top.current?.scrollIntoView({ block: "start" });
                }}
              />
            </div>
          )
        ) : null}
      </Swap>
    </section>
  );
}

/* ── 제안 피드백 ─────────────────────────────────────────────────────── */

function FeedbackTab({ childId }: { childId: string }) {
  const queryClient = useQueryClient();
  const [pendingId, setPendingId] = useState<string | null>(null);

  /**
   * 받은 제안 목록 (#284) — 개인화와 채택한 일반 추천(#509)이 같이 온다.
   * ⚠️ 첫 장(20건)만 그린다 — `next_cursor` 로 더 보기는 아직 없다 (#316 하지 않는 것).
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
      suggestion: ReceivedSuggestion;
      feedback: SuggestionFeedback;
    }) =>
      api.patch<SuggestionFeedbackResponse>(
        `/children/${childId}/suggestions/${suggestion.id}/feedback`,
        { feedback } satisfies SuggestionFeedbackRequest,
      ),
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

      <Swap id={phaseOf(received)}>
        {received.isPending ? (
          <SkeletonGroup label="받은 제안을 불러오는 중">
            <SuggestionFeedbackListSkeleton />
          </SkeletonGroup>
        ) : null}

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
      </Swap>

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
