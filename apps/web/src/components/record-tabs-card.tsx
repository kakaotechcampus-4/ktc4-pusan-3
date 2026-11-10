"use client";

import {
  skipToken,
  useIsMutating,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import {
  Check,
  ChevronDown,
  ChevronUp,
  Circle,
  Clock,
  Info,
  Trash2,
  X,
  type LucideIcon,
} from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";

import { observationLabel } from "@/components/domain-chip";
import { ObservationRow, observationDetail } from "@/components/observation-row";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { ICON_SIZE, ICON_STROKE } from "@/components/ui/icon";
import { IconTile } from "@/components/ui/icon-tile";
import { Spinner } from "@/components/ui/spinner";
import { PanelTabs } from "@/components/ui/tabs";
import { cn } from "@/lib/cn";
import { cancelDeleteProposal, confirmDeleteProposal } from "@/lib/api/operations";
import { qk } from "@/lib/api/queryKeys";
import {
  OBSERVATION_KINDS,
  type CalendarEvent,
  type CardTab,
  type CardTabKind,
  type DeleteProposalCard as DeleteProposalCardData,
  type ObservationKind,
  type ProposalTab,
} from "@/lib/api/types";
import { useIdempotencyKey } from "@/lib/api/use-idempotency-key";
import { refreshAfterRun } from "@/lib/chat-history";
import { closedBy, stuckBy, type Settled } from "@/lib/delete-proposal";
import { formatEventTime } from "@/lib/format";

/**
 * 04 대화의 기록 · 일정 카드 (#535) — 대화로 찾거나 지울 때 **AI 문장이 아니라 저장된 행을 그대로** 보여 준다.
 *
 * - `FoundTabsCard` — 조회 답변 · 수정 후보. 보기 전용이다.
 * - `DeleteProposalCard` — 삭제 제안. 🚨 **AI 는 지우지 않았다.** 보호자가 체크를 고치고 두 번 눌러야
 *   서버가 **후보 안의 체크한 것만** 지운다. 채팅 "응" 을 모델이 해석하던 자리를 버튼으로 바꾼 것이다
 *   (서버 `docs/api/delete-proposal-v1.md`).
 *
 * 둘 다 탭 하나가 조회 tool 하나다 — 식사 · 건강 · 교육 · 놀이 · 생활습관 · 일정. 순서는 서버가 정한 그대로다.
 * 규칙 원문은 `docs/web/chat-run-v1.md` §5.
 *
 * 🚨 **승인 게이트 ㉢ 이지만 표식(`btn-approve` · `caution`)을 쓰지 않는다** — 표식은 ㉠ ㉡ 전용이다 (최상위 §2 · #754).
 *    "파괴적 확정" 이라 `btn-danger` 다 (디자인 시스템 §7 버튼 · §11).
 * 🚨 **기록 줄에 도메인 색을 쓰지 않는다** — 저장된 기록 줄(`ObservationRow`)과 같은 이유다.
 */

/* ── 공통 ─────────────────────────────────────────────────────────────── */

/** 서버 탭 순서와 같다. 한 줄 요약(`result`)의 키도 이 순서로 읽는다. */
const KIND_ORDER: readonly CardTabKind[] = [
  "observation_food",
  "observation_health",
  "observation_education",
  "observation_activity",
  "observation_routine",
  "event",
];

function isObservationKind(kind: string): kind is ObservationKind {
  return (OBSERVATION_KINDS as readonly string[]).includes(kind);
}

/** 탭 · 요약의 이름. 🚨 모르는 종류가 와도 빈칸으로 두지 않는다 — 서버가 표를 먼저 늘린 날이다. */
function kindLabel(kind: string): string {
  if (kind === "event") return "일정";
  return isObservationKind(kind) ? observationLabel(kind) : "기록";
}

/** 화면 용어 — 관찰은 "기록", 일정은 "일정" (apps/web/CLAUDE.md §3 컴포넌트). */
function nounOf(kind: string): "기록" | "일정" {
  return kind === "event" ? "일정" : "기록";
}

/** 탭에 선 기록 · 일정의 id, 화면 순서 그대로. 한 탭에는 둘 중 한쪽만 찬다. */
function rowIds(tab: CardTab): string[] {
  return [...tab.records.map((r) => r.id), ...tab.events.map((e) => e.id)];
}

/** "기록 7건, 일정 3건" — 0건인 쪽은 빼고 센다. */
function countPhrase(tabs: readonly CardTab[]): string {
  const records = tabs.reduce((sum, tab) => sum + tab.records.length, 0);
  const events = tabs.reduce((sum, tab) => sum + tab.events.length, 0);
  return [records > 0 ? `기록 ${records}건` : null, events > 0 ? `일정 ${events}건` : null]
    .filter(Boolean)
    .join(", ");
}

/**
 * 🚨 **"최근 40건" 은 서버의 한도다** — 건수를 화면이 세서 말하지 않는다(전체 건수는 오지 않는다).
 *    잘린 탭에만 선다. 보이지 않던 기록은 후보가 아니라 지워지지 않는다는 것도 같이 말한다.
 */
function TruncatedNote({ noun, deleting }: { noun: string; deleting: boolean }) {
  return (
    <p className="bg-surface-muted text-body-sm text-ink-muted rounded-field mx-4 mt-3 px-3 py-2.5">
      최근 40건만 보여 드렸어요.{" "}
      {deleting
        ? `여기 없는 앞선 ${noun}은 그대로 둬요. 그 ${noun}도 지우려면 기간을 좁혀 다시 말씀해 주세요.`
        : `그 전 ${noun}은 기간을 좁혀 다시 물어봐 주세요.`}
    </p>
  );
}

/** 일정 한 줄 (보기 전용). 🚨 준비물 체크는 여기서 바꾸지 않는다 — 09 캘린더에서 한다 (#535 범위 밖). */
function EventRow({ event }: { event: CalendarEvent }) {
  return (
    <div className="py-3 first:pt-0 last:pb-0">
      <p className="text-body text-ink">{event.title}</p>
      <p className="text-caption text-ink-subtle mt-1">
        {formatEventTime(event.starts_at, event.all_day)}
      </p>
      {event.items.length > 0 ? (
        <div className="mt-2">
          <p className="text-label text-ink-muted">준비물</p>
          <ul className="mt-1 grid grid-cols-2 gap-x-3 gap-y-1">
            {event.items.map((item) => (
              <li
                key={item.item_id}
                className="text-body-sm text-ink-muted flex min-w-0 items-start gap-1.5"
              >
                {item.is_prepared ? (
                  <Check
                    aria-hidden
                    size={ICON_SIZE.sm}
                    strokeWidth={ICON_STROKE}
                    className="text-ink-subtle mt-0.5 shrink-0"
                  />
                ) : (
                  <Circle
                    aria-hidden
                    size={ICON_SIZE.sm}
                    strokeWidth={ICON_STROKE}
                    className="text-ink-subtle mt-0.5 shrink-0"
                  />
                )}
                <span className="min-w-0 break-words">
                  {item.item_name}
                  <span className="sr-only">{item.is_prepared ? " 챙김" : " 아직 안 챙김"}</span>
                </span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}

/* ── 조회 카드 ────────────────────────────────────────────────────────── */

/**
 * 찾은 기록 · 일정 (조회 답변 · 수정 후보). 🚨 **보기 전용이다** — 이 카드에서는 아무것도 바꾸지 않는다.
 * 수정은 지금처럼 채팅으로 한다 (#535 범위 밖).
 */
export function FoundTabsCard({ tabs }: { tabs: readonly CardTab[] }) {
  const [active, setActive] = useState<string>(tabs[0]?.kind ?? "");
  const panelId = useId();
  const current = tabs.find((tab) => tab.kind === active) ?? tabs[0];
  if (!current) return null;

  return (
    <section data-tour="found-tabs" className="flex flex-col gap-2">
      <h3 className="text-label text-brand">찾은 {countPhrase(tabs)}</h3>
      <div className="rounded-card bg-surface border-line overflow-hidden border">
        <PanelTabs
          items={tabs.map((tab) => ({
            key: tab.kind,
            label: kindLabel(tab.kind),
            count: String(rowIds(tab).length),
            countLabel: `${rowIds(tab).length}건`,
          }))}
          active={current.kind}
          onChange={setActive}
          label="찾은 기록과 일정 종류"
          panelId={panelId}
          scroll={tabs.length > 3}
        />
        <div
          id={panelId}
          role="tabpanel"
          aria-labelledby={`${panelId}-tab-${current.kind}`}
          tabIndex={0}
        >
          {current.truncated ? (
            <TruncatedNote noun={nounOf(current.kind)} deleting={false} />
          ) : null}
          <div className="divide-line flex flex-col divide-y px-4 py-3">
            {current.records.map((record) => (
              <ObservationRow key={record.id} observation={record} />
            ))}
            {current.events.map((event) => (
              <EventRow key={event.id} event={event} />
            ))}
          </div>
        </div>
      </div>
    </section>
  );
}

/* ── 삭제 카드 ────────────────────────────────────────────────────────── */

/** 24시간 + 여유. `setTimeout` 은 2^31-1ms(약 24.8일)를 넘기면 바로 불린다. */
const MAX_TIMER_MS = 2_147_483_647;

/**
 * 삭제 제안 카드. 결정 전이면 체크 카드, 끝났으면 한 줄 요약이다.
 *
 * 🚨 **끝난 상태의 정본은 버튼 응답이다** (`qk.deleteProposal`). run 화면의 카드는 대화 스토어에 `pending` 으로
 *    굳어 있고 지난 대화도 받은 순간의 상태라, 응답을 거기 두지 않으면 화면을 떠났다 오면 이미 지운 카드에 체크와
 *    버튼이 다시 선다.
 * 🚨 **run 화면의 카드는 늘 `pending` 으로 온다** — 띄워 둔 채 `expires_at` 이 지나면 화면이 닫힌 카드로 바꾼다.
 *    그 전에 누르면 409 `proposal_expired` 가 와서 같은 요약으로 접힌다.
 */
export function DeleteProposalCard({
  childId,
  card,
}: {
  childId: string;
  card: DeleteProposalCardData;
}) {
  const settledKey = qk.deleteProposal(childId, card.proposal_id);
  const { data: settled } = useQuery<Settled>({
    queryKey: settledKey,
    // 받아 오는 길이 없다 — 버튼 응답을 `setQueryData` 로 넣는다. 무효화가 와도 다시 받지 않는다.
    queryFn: skipToken,
    staleTime: Infinity,
    gcTime: Infinity,
  });
  const queryClient = useQueryClient();
  const settle = (next: Settled) => queryClient.setQueryData<Settled>(settledKey, next);

  const timedOut = useTimedOut(card.status === "pending" ? card.expires_at : null);
  /**
   * 🚨 **누른 요청이 나가 있는 동안에는 타이머로 접지 않는다** — 서버가 이미 잠금을 잡고 지우는 중일 수 있다.
   *    응답(지움 · 409 만료)이 오면 그것으로 접힌다.
   */
  const busy = useIsMutating({ mutationKey: settledKey }) > 0;
  /** 방금 이 화면에서 누른 끝인가 — 요약이 포커스를 받는다 (누른 버튼이 카드째 사라진다). */
  const [pressed, setPressed] = useState(false);

  /**
   * `timedOut` 은 화면 시계로 접은 것이다. 🚨 서버가 말한 만료(`expired` · 409)와 문구가 다르다 — 앞서 연결이 끊긴
   * 확정이 서버에서는 지웠을 수 있어서 "아무것도 지우지 않았어요" 라고 단정하지 않는다.
   */
  const status: SummaryStatus | "pending" =
    settled?.status ?? (card.status === "pending" && timedOut && !busy ? "timedOut" : card.status);

  if (status !== "pending") {
    return (
      <ProposalSummary
        status={status}
        result={settled ? settled.result : card.result}
        takeFocus={pressed}
      />
    );
  }

  // 🚨 그사이 후보가 다 지워진 결정 전 카드다 (다른 보호자 · 다른 대화에서). 지울 것이 없어서 버튼을 세우지 않는다 —
  //    그대로 두면 24시간 뒤 닫힌다. 아무것도 지우지 않는 카드라 취소를 따로 누를 이유가 없다.
  if (card.tabs.length === 0) {
    return (
      <ProposalNote icon={Info} title="지울 후보가 남아 있지 않아요">
        이 확인에 있던 것은 그사이 모두 지워졌어요. 더 지울 것이 없어요.
      </ProposalNote>
    );
  }

  return (
    <ProposalPicker
      childId={childId}
      card={card}
      onSettled={(next) => {
        setPressed(true);
        settle(next);
      }}
    />
  );
}

/**
 * `expires_at` 이 지났는가. 🚨 **날짜 계산이 아니다** — 서버가 준 절대 시각 하나와 지금을 비교할 뿐이다
 * (apps/web/CLAUDE.md §4 의 "날짜 · 나이를 계산하지 않는다" 는 표시 문구 얘기다). 맞히지 못해도 서버가 409 로 막는다.
 */
function useTimedOut(expiresAt: string | null): boolean {
  const [timedOut, setTimedOut] = useState(false);
  useEffect(() => {
    if (expiresAt === null) return;
    const left = Date.parse(expiresAt) - Date.now();
    if (Number.isNaN(left) || left > MAX_TIMER_MS) return;
    const timer = setTimeout(() => setTimedOut(true), Math.max(0, left));
    return () => clearTimeout(timer);
  }, [expiresAt]);
  return timedOut;
}

/** "식사 30건, 놀이 12건" — 서버 탭 순서로, 0건은 빼고. */
function resultPhrase(counts: Record<string, number>): string {
  const keys = [
    ...KIND_ORDER.filter((kind) => kind in counts),
    ...Object.keys(counts).filter((kind) => !(KIND_ORDER as readonly string[]).includes(kind)),
  ];
  return keys
    .filter((kind) => (counts[kind] ?? 0) > 0)
    .map((kind) => `${kindLabel(kind)} ${counts[kind]}건`)
    .join(", ");
}

function ProposalPicker({
  childId,
  card,
  onSettled,
}: {
  childId: string;
  card: DeleteProposalCardData;
  onSettled: (settled: Settled) => void;
}) {
  const queryClient = useQueryClient();
  const idem = useIdempotencyKey();
  const panelId = useId();
  const tabs = card.tabs;

  /**
   * 🚨 **두 묶음은 처음 체크로 고정한다** (#535). 체크를 풀 때마다 행이 "나머지" 로 옮겨 가면 누르던 손 밑에서
   *    줄이 사라진다. 처음 기본 체크(`picked_ids`)를 처음 렌더에서 한 번만 읽는다.
   */
  const [picked] = useState(
    () => new Set(tabs.flatMap((tab) => tab.picked_ids.filter((id) => rowIds(tab).includes(id)))),
  );
  const [checked, setChecked] = useState<ReadonlySet<string>>(() => new Set(picked));
  const [active, setActive] = useState<string>(tabs[0]!.kind);
  /** 탭마다 "나머지" 를 펼쳤는가. 고른 것이 하나도 없는 탭은 처음부터 펼친다. */
  const [restOpen, setRestOpen] = useState<Record<string, boolean>>({});
  const [confirming, setConfirming] = useState(false);

  const current = tabs.find((tab) => tab.kind === active) ?? tabs[0]!;
  const checkedIn = (tab: ProposalTab) => rowIds(tab).filter((id) => checked.has(id));
  /** 보낼 id — 화면 순서 그대로. */
  const checkedIds = tabs.flatMap(checkedIn);
  const total = checkedIds.length;
  const checkedPhrase = resultPhrase(
    Object.fromEntries(tabs.map((tab) => [tab.kind, checkedIn(tab).length])),
  );
  const nouns = new Set(tabs.map((tab) => nounOf(tab.kind)));
  const what = nouns.size > 1 ? "기록과 일정" : nouns.has("일정") ? "일정" : "기록";

  const setMany = (ids: readonly string[], on: boolean) =>
    setChecked((prev) => {
      const next = new Set(prev);
      for (const id of ids) {
        if (on) next.add(id);
        else next.delete(id);
      }
      return next;
    });

  /**
   * 🚨 **키는 체크한 id 에 묶는다** (`current(payload)`). 실패한 뒤 체크를 바꿔 다시 누르면 다른 본문이라
   *    "같은 키 · 다른 본문" 422 에 갇힌다. 같은 체크로 다시 누르면 같은 키다 — 응답만 잃었어도 두 번 지우지 않는다.
   * 🚨 낙관적 업데이트를 쓰지 않는다 — 응답의 `proposal` 로만 접는다 (apps/web/CLAUDE.md §3 에러).
   */
  const mutationKey = qk.deleteProposal(childId, card.proposal_id);
  const confirm = useMutation({
    mutationKey,
    mutationFn: (ids: string[]) =>
      confirmDeleteProposal(childId, card.proposal_id, { ids }, idem.current(ids.join(","))),
    onSuccess: ({ proposal }) => {
      idem.rotate();
      onSettled({
        status: proposal.status === "pending" ? "closed" : proposal.status,
        result: proposal.result,
      });
      // 기록 · 일정 · 기억이 바뀌었다. 대화 목록은 오늘 장만 갈아 끼운다 (`refreshAfterRun` · chat-run-v1 §3).
      void refreshAfterRun(queryClient, childId);
    },
    onError: (error) => {
      const closed = closedBy(error);
      if (closed) onSettled(closed);
    },
  });

  const cancel = useMutation({
    mutationKey,
    mutationFn: () => cancelDeleteProposal(childId, card.proposal_id),
    onSuccess: ({ proposal }) =>
      onSettled({
        status: proposal.status === "pending" ? "closed" : proposal.status,
        result: proposal.result,
      }),
    onError: (error) => {
      const closed = closedBy(error);
      if (closed) onSettled(closed);
    },
  });

  const busy = confirm.isPending || cancel.isPending;
  /** 다시 눌러도 같은 응답이 오는 실패 (403 · 404 · 422). 버튼을 잠그고 이유를 말한다 (`stuckBy`). */
  const stuck = stuckBy(confirm.error) ?? stuckBy(cancel.error);

  /** 확인 띠를 열면 화면 안으로 끌어오고, "그만두기" 로 닫으면 포커스를 "N건 삭제하기" 로 돌려준다 (09 일기 지우기와 같다). */
  const confirmRef = useRef<HTMLDivElement>(null);
  const askRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (confirming) confirmRef.current?.scrollIntoView({ block: "nearest" });
  }, [confirming]);

  const pickedRows = rowIds(current).filter((id) => picked.has(id));
  const restRows = rowIds(current).filter((id) => !picked.has(id));
  const restShown = restOpen[current.kind] ?? pickedRows.length === 0;
  const allPickedOn = pickedRows.every((id) => checked.has(id));
  const noun = nounOf(current.kind);

  const row = (id: string) => (
    <ProposalRow
      key={id}
      tab={current}
      id={id}
      checked={checked.has(id)}
      disabled={busy || confirming}
      onChange={(on) => setMany([id], on)}
    />
  );

  return (
    <section data-tour="delete-proposal" className="flex flex-col gap-2">
      <h3 className="text-label text-brand">지울 {what} 고르기</h3>
      <div className="rounded-card bg-surface border-line overflow-hidden border">
        <PanelTabs
          items={tabs.map((tab) => ({
            key: tab.kind,
            label: kindLabel(tab.kind),
            // 🚨 "체크 수/후보 수" — 탭을 넘기지 않아도 어디서 몇 건을 지우는지 보인다.
            count: `${checkedIn(tab).length}/${rowIds(tab).length}`,
            countLabel: `${rowIds(tab).length}건 중 ${checkedIn(tab).length}건 고름`,
          }))}
          active={current.kind}
          onChange={setActive}
          label={`지울 ${what} 종류`}
          panelId={panelId}
          scroll={tabs.length > 3}
        />

        <div
          id={panelId}
          role="tabpanel"
          aria-labelledby={`${panelId}-tab-${current.kind}`}
          className="max-h-[60vh] overflow-y-auto overscroll-contain"
        >
          {current.truncated ? <TruncatedNote noun={noun} deleting /> : null}

          {pickedRows.length > 0 ? (
            <div className="px-4 pt-2">
              <div className="flex items-center justify-between gap-2">
                <p className="text-label text-ink">
                  요청에 맞는 {noun} {pickedRows.length}건
                </p>
                <Button
                  variant="tertiary"
                  size="compact"
                  disabled={busy || confirming}
                  onClick={() => setMany(pickedRows, !allPickedOn)}
                >
                  {allPickedOn ? "전체 해제" : "전체 선택"}
                </Button>
              </div>
              <div className="divide-line flex flex-col divide-y">{pickedRows.map(row)}</div>
            </div>
          ) : null}

          {restRows.length > 0 ? (
            <div className={cn("mx-4 mt-1", pickedRows.length > 0 && "border-line border-t")}>
              <button
                type="button"
                aria-expanded={restShown}
                onClick={() => setRestOpen((prev) => ({ ...prev, [current.kind]: !restShown }))}
                className="text-label text-ink-muted min-h-touch active:text-ink flex w-full items-center gap-1.5 text-left"
              >
                {restShown ? (
                  <ChevronUp aria-hidden size={ICON_SIZE.sm} strokeWidth={ICON_STROKE} />
                ) : (
                  <ChevronDown aria-hidden size={ICON_SIZE.sm} strokeWidth={ICON_STROKE} />
                )}
                나머지 {noun} {restRows.length}건 {restShown ? "접기" : "보기"}
              </button>
              {restShown ? (
                <div className="divide-line flex flex-col divide-y">{restRows.map(row)}</div>
              ) : null}
            </div>
          ) : null}
        </div>

        {confirming ? (
          /*
            🚨 **한 번 더 묻는다** — 지운 기록은 되살릴 수 없다. 묻는 문장은 **체크한 수**로 말한다(아직 지우기 전이다).
               지운 뒤의 요약은 응답의 `result` 로만 말한다 — 그사이 다른 경로로 지워진 행은 세지 않는다.
            🚨 확인 띠는 카드 바닥의 띠다 (09 일기 지우기와 같은 모양 · 카드 안에 카드를 겹치지 않는다).
            🚨 **대화에 적은 말이 남는다는 것을 말한다** (#748). 서버는 기록을 `deleted` 로 표시만 하고, 보호자가 쓴 문장은
               지난 대화 말풍선(`chat_turn`)에 그대로 남는다. 안 말하면 대화에 쓴 말까지 사라진다고 읽힌다.
               09 일기 지우기처럼 한 줄로 줄이지 않는 이유 — 여러 건을 한 번에 지우고, 지운 뒤 남는 것이 있다.
               기록 탭의 삭제 확인(#736) · 고객센터(#750)와 같은 말을 쓴다.
          */
          <div
            ref={confirmRef}
            role="group"
            aria-label="삭제 확인"
            className="bg-surface-muted border-line flex scroll-mb-28 flex-col gap-3 border-t px-4 py-3.5"
          >
            <div>
              <p className="text-body text-ink">{checkedPhrase}을 지울까요?</p>
              <p className="text-body-sm text-ink-muted mt-1">
                지운 것은 되살릴 수 없고, 앞으로 추천에도 쓰지 않아요. 체크하지 않은 것은 그대로
                둬요.
              </p>
              <p className="text-body-sm text-ink-muted mt-1">
                대화에 적은 말은 지난 대화에 그대로 남아요.
              </p>
            </div>
            <div className="flex flex-wrap gap-2">
              <Button
                variant="tertiary"
                size="compact"
                className="flex-1"
                disabled={busy}
                onClick={() => {
                  setConfirming(false);
                  // 🚨 잠근 이유(`stuck`)는 지우지 않는다 — 지우면 다시 눌러도 같은 응답인 버튼이 살아난다.
                  if (!stuck) confirm.reset();
                  askRef.current?.focus();
                }}
              >
                그만두기
              </Button>
              <Button
                variant="danger"
                size="compact"
                className="flex-1"
                disabled={busy || stuck !== null}
                onClick={() => confirm.mutate(checkedIds)}
              >
                {confirm.isPending ? <Spinner /> : null}
                {confirm.isPending ? "지우는 중이에요" : "삭제"}
              </Button>
            </div>
            {/* 🚨 실패를 빨강으로 칠하지 않는다. 결과를 모르는 실패(연결)도 있어서 "아무것도 안 지웠어요" 라고
                단정하지 않는다 — 같은 체크로 다시 누르면 같은 키라 두 번 지우지 않는다. */}
            {stuck ? (
              <p role="status" className="text-body-sm text-ink-muted">
                {stuck}
              </p>
            ) : confirm.isError && !closedBy(confirm.error) ? (
              <p role="status" className="text-body-sm text-ink-muted">
                삭제를 끝내지 못했어요. 다시 눌러도 두 번 지워지지 않아요.
              </p>
            ) : null}
          </div>
        ) : (
          <div className="border-line flex flex-col gap-2 border-t px-4 py-3">
            <div className="flex flex-wrap gap-2">
              <Button
                variant="secondary"
                size="compact"
                className="flex-1"
                disabled={busy || stuck !== null}
                onClick={() => cancel.mutate()}
              >
                {cancel.isPending ? <Spinner /> : null}
                지우지 않을래요
              </Button>
              {/* 🚨 0건이면 막고, 막힌 이유를 버튼이 말한다 — 이유 없이 꺼진 버튼을 두지 않는다. */}
              <Button
                ref={askRef}
                variant="danger"
                size="compact"
                className="flex-1"
                disabled={busy || stuck !== null || total === 0}
                onClick={() => setConfirming(true)}
              >
                {total === 0 ? "고른 것이 없어요" : `${total}건 삭제하기`}
              </Button>
            </div>
            {/* 🚨 "아무것도 지우지 않았어요" 를 붙이지 않는다 — 앞서 연결이 끊긴 확정이 서버에서는 지웠을 수 있다. */}
            {stuck ? (
              <p role="status" className="text-body-sm text-ink-muted">
                {stuck}
              </p>
            ) : cancel.isError && !closedBy(cancel.error) ? (
              <p role="status" className="text-body-sm text-ink-muted">
                지금은 취소하지 못했어요. 다시 눌러 주세요.
              </p>
            ) : null}
          </div>
        )}
      </div>
    </section>
  );
}

/** 체크 한 줄. 무엇인지는 라벨이, 언제 · 무엇에 관한 것인지는 설명 줄이 진다. */
function ProposalRow({
  tab,
  id,
  checked,
  disabled,
  onChange,
}: {
  tab: ProposalTab;
  id: string;
  checked: boolean;
  disabled: boolean;
  onChange: (checked: boolean) => void;
}) {
  const record = tab.records.find((r) => r.id === id);
  const event = record ? undefined : tab.events.find((e) => e.id === id);
  const label = record?.raw_text ?? event?.title ?? "";
  const description = record
    ? [record.observed_label, observationDetail(record)].filter(Boolean).join(", ")
    : event
      ? formatEventTime(event.starts_at, event.all_day)
      : undefined;

  return (
    <Checkbox
      checked={checked}
      onChange={onChange}
      disabled={disabled}
      label={label}
      description={description || undefined}
      className="py-3"
    />
  );
}

/* ── 끝난 카드 ────────────────────────────────────────────────────────── */

/** 끝난 카드의 갈래. `timedOut` 은 화면 시계로 접은 것이다 (`DeleteProposalCard` 의 `status`). */
type SummaryStatus = Settled["status"] | "timedOut";

const SUMMARY: Record<SummaryStatus, { icon: LucideIcon; title: string; body: string }> = {
  deleted: { icon: Trash2, title: "", body: "체크하지 않은 것은 그대로 있어요." },
  cancelled: { icon: X, title: "삭제를 취소했어요.", body: "아무것도 지우지 않았어요." },
  expired: {
    icon: Clock,
    title: "시간이 지나 삭제 확인이 닫혔어요.",
    body: "아무것도 지우지 않았어요. 지우려면 다시 말씀해 주세요.",
  },
  timedOut: {
    icon: Clock,
    title: "시간이 지나 삭제 확인이 닫혔어요.",
    body: "이 확인으로는 더 지울 수 없어요. 지우려면 다시 말씀해 주세요.",
  },
  // 409 `proposal_closed` — 지웠는지 취소했는지 응답에 없다. 대화를 다시 열면 서버가 그때 상태로 준다.
  closed: {
    icon: Info,
    title: "이미 처리된 삭제 확인이에요.",
    body: "어떻게 처리됐는지는 대화를 다시 열면 보여요.",
  },
};

/**
 * 끝난 삭제 카드 한 줄 (#535). 🚨 **지운 원문을 다시 보이지 않는다** — 서버도 끝난 카드에는 후보 행을 싣지 않는다.
 * 🚨 건수는 응답의 `result` 다. 체크한 수가 아니다.
 */
function ProposalSummary({
  status,
  result,
  takeFocus,
}: {
  status: SummaryStatus;
  result: Record<string, number> | null;
  takeFocus: boolean;
}) {
  const summary = SUMMARY[status];
  const phrase = status === "deleted" && result ? resultPhrase(result) : "";
  const title =
    status === "deleted"
      ? phrase
        ? `${phrase}을 삭제했어요.`
        : "고른 것은 그사이 이미 지워져 있었어요."
      : summary.title;

  return (
    <ProposalNote icon={summary.icon} title={title} takeFocus={takeFocus}>
      {status === "deleted" && !phrase ? "더 지운 것은 없어요." : summary.body}
    </ProposalNote>
  );
}

function ProposalNote({
  icon,
  title,
  takeFocus = false,
  children,
}: {
  icon: LucideIcon;
  title: string;
  /** 버튼을 누른 자리가 카드째 사라졌다 — 포커스가 `<body>` 로 떨어지지 않게 이 줄이 받는다. */
  takeFocus?: boolean;
  children: string;
}) {
  return (
    <div
      ref={(node) => {
        if (node && takeFocus) node.focus();
      }}
      tabIndex={takeFocus ? -1 : undefined}
      role="status"
      className="rounded-card bg-surface border-line flex items-start gap-3 border p-4"
    >
      <IconTile icon={icon} tone="neutral" />
      <div className="min-w-0">
        <p className="text-body text-ink">{title}</p>
        <p className="text-body-sm text-ink-muted mt-1">{children}</p>
      </div>
    </div>
  );
}
