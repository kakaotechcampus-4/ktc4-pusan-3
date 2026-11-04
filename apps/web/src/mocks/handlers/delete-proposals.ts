import { http, HttpResponse } from "msw";

import {
  CONFIRM_DELETE_MAX_IDS,
  type CalendarEvent,
  type CardTab,
  type ConfirmDeleteProposalRequest,
  type DeleteProposal,
  type DeleteProposalCard,
  type DeleteProposalResponse,
  type DeleteProposalStatus,
  type FoundTabs,
  type ObservationPromotable,
  type ProposalTab,
} from "@/lib/api/types";

import { CHILD_ID, daysAgo, hoursFromNow } from "../fixtures";
import { apiError, networkDelay, url } from "./helpers";
import { withIdempotency } from "./idempotency";

/**
 * 04 대화의 조회 · 삭제 카드 (#535) — 카드에 실을 행과 삭제 제안 확정 · 취소.
 *
 * 서버 정본은 `apps/api/app/api/v1/routers/delete_proposals.py` · `docs/api/delete-proposal-v1.md`. 목이 흉내 내는 것:
 *
 *   ① 후보 안의 체크한 것만 지운다 — 하나라도 밖이면 422 `not_in_proposal` 이고 아무것도 안 지운다
 *   ② 24시간이 지났으면 409 `proposal_expired`, 이미 끝났으면 409 `proposal_closed` (취소를 다시 누르면 같은 응답)
 *   ③ 확정은 Idempotency-Key 필수 — 같은 키로 다시 오면 처음 응답 재생 (`withIdempotency`)
 *   ④ 만료는 저장하지 않고 읽을 때 판정한다 — 지난 대화에서만 `expired` 로 온다
 *
 * 🚨 **목은 기록을 실제로 지우지 않는다** — 07 기록 탭의 목록은 그대로다. 카드가 버튼 응답으로 접히는지,
 *    지난 대화가 끝난 카드를 한 줄로 그리는지만 본다. 픽스처는 지어낸 문장이다 (최상위 §9).
 */

/* ── 카드에 실을 행 ───────────────────────────────────────────────────── */

const DAY_LABELS = ["오늘", "어제", "2일 전", "3일 전", "4일 전", "5일 전", "6일 전"];

function record(
  id: string,
  kind: ObservationPromotable["kind"],
  subject: string,
  rawText: string,
  days: number,
): ObservationPromotable {
  return {
    id,
    child_id: CHILD_ID,
    kind,
    raw_text: rawText,
    subject,
    polarity: 1,
    strong_signals: [],
    confidence_source: "parent_direct",
    status: "active",
    observed_from: daysAgo(days),
    observed_to: daysAgo(days),
    observed_label: DAY_LABELS[days] ?? `${Math.floor(days / 7)}주 전`,
    affinity: null,
    domain_fields: {},
  };
}

function event(
  id: string,
  title: string,
  daysFromToday: number,
  items: Array<[string, boolean]>,
): CalendarEvent {
  return {
    id,
    title,
    event_type: "episodic",
    starts_at: `${daysAgo(-daysFromToday)}T10:00:00+09:00`,
    ends_at: null,
    all_day: false,
    category: "etc",
    created_by: "caregiver",
    source_notice_id: null,
    source_refs: [],
    items: items.map(([name, prepared], i) => ({
      item_id: `${id}_i${i}`,
      item_name: name,
      is_prepared: prepared,
      prepared_at: prepared ? hoursFromNow(-2) : null,
    })),
    reminder: null,
  };
}

/** `found_tabs` 시나리오 — "이번주 일정과 기록 보여줘". 탭이 넷이라 탭 줄이 옆으로 민다. */
export function foundTabsFixture(): FoundTabs {
  return {
    tabs: [
      {
        kind: "observation_food",
        truncated: false,
        records: [
          record("lk_f1", "observation_food", "브로콜리", "브로콜리는 골라내고 안 먹었어", 4),
          record("lk_f2", "observation_food", "미역국", "어린이집 점심 미역국 다 먹었대", 2),
          record("lk_f3", "observation_food", "고구마", "아침에 고구마 반 개 먹었어", 0),
        ],
        events: [],
      },
      {
        kind: "observation_education",
        truncated: false,
        records: [
          record("lk_e1", "observation_education", "숫자 세기", "스티커 붙이면서 열까지 셌대", 5),
        ],
        events: [],
      },
      {
        kind: "observation_activity",
        truncated: false,
        records: [
          record("lk_a1", "observation_activity", "블록", "블록으로 기차 만들고 한참 놀았어", 3),
          record("lk_a2", "observation_activity", "미끄럼틀", "놀이터 미끄럼틀 열 번 넘게 탔어", 1),
        ],
        events: [],
      },
      {
        kind: "event",
        truncated: false,
        records: [],
        events: [
          event("lk_ev1", "예방접종", -2, [
            ["아기수첩", true],
            ["보험증", false],
          ]),
          event("lk_ev2", "할머니댁 방문", 2, []),
        ],
      },
    ],
  };
}

const MEALS = [
  "아침에 바나나 하나",
  "점심 카레 반 그릇",
  "간식으로 요거트",
  "저녁 김밥 두 줄",
  "아침 계란찜",
  "간식으로 사과 반쪽",
];

const PLAYS = ["블록 쌓기", "그네 타기", "공 던지기", "색칠놀이", "퍼즐 맞추기", "모래놀이"];

/**
 * `delete_proposal` 시나리오 — "저번주 식사랑 놀이 기록 중에 간식은 빼고 지워줘".
 *
 * - 식사 40건(잘림) · 간식은 AI 가 고르지 않았다 → "나머지" 로 접혀 선다
 * - 놀이 12건 · 전부 골랐다
 * - 일정 2건 · 하나도 안 골랐다 → "나머지" 가 처음부터 펼쳐진다
 */
function proposalTabs(): ProposalTab[] {
  const food = Array.from({ length: 40 }, (_, i) =>
    record(
      `dp_f${i + 1}`,
      "observation_food",
      "식사",
      `${MEALS[i % MEALS.length]} (${i + 1})`,
      7 + Math.floor(i / 6),
    ),
  );
  const play = Array.from({ length: 12 }, (_, i) =>
    record(
      `dp_a${i + 1}`,
      "observation_activity",
      "놀이",
      `${PLAYS[i % PLAYS.length]}`,
      7 + (i % 7),
    ),
  );
  const events = [event("dp_ev1", "소아과 예약", -8, []), event("dp_ev2", "문화센터", -9, [])];
  return [
    {
      kind: "observation_food",
      truncated: true,
      records: food,
      events: [],
      picked_ids: food.filter((r) => !r.raw_text.startsWith("간식")).map((r) => r.id),
    },
    {
      kind: "observation_activity",
      truncated: false,
      records: play,
      events: [],
      picked_ids: play.map((r) => r.id),
    },
    { kind: "event", truncated: false, records: [], events, picked_ids: [] },
  ];
}

/* ── 삭제 제안 ────────────────────────────────────────────────────────── */

interface StoredProposal {
  childId: string;
  /** 처음 만든 카드(결정 전 · 후보 전부). */
  card: DeleteProposalCard;
  status: Exclude<DeleteProposalStatus, "expired">;
  result: Record<string, number> | null;
}

const proposals = new Map<string, StoredProposal>();
let seq = 0;

export function resetDeleteProposals(): void {
  proposals.clear();
  seq = 0;
}

/**
 * run 이 제안을 남긴다 — 서버는 Memory commit 때 `delete_proposal` 한 행을 같이 저장한다.
 * @param expiresInMs 만료까지. 기본 24시간. `delete_proposal_expiring` 은 몇십 초로 줄여 화면이 닫는 것을 본다.
 */
export function proposeDelete(
  childId: string,
  expiresInMs = 24 * 60 * 60 * 1000,
): DeleteProposalCard {
  seq += 1;
  const card: DeleteProposalCard = {
    proposal_id: `dp_${seq}`,
    status: "pending",
    expires_at: new Date(Date.now() + expiresInMs).toISOString(),
    result: null,
    tabs: proposalTabs(),
  };
  proposals.set(card.proposal_id, { childId, card, status: "pending", result: null });
  return card;
}

/** 🚨 만료는 읽을 때 판정한다 — 서버 `effective_status` 와 같다. */
function effectiveStatus(stored: StoredProposal): DeleteProposalStatus {
  if (stored.status === "pending" && Date.now() >= Date.parse(stored.card.expires_at)) {
    return "expired";
  }
  return stored.status;
}

/**
 * 지난 대화에 실을 지금 상태 (서버 `chat_replies` 가 다시 읽는 것과 같다). 결정 전이면 후보 그대로, 끝났으면
 * `tabs: []` 와 상태 · 건수만. 🚨 목이 모르는 제안(새로고침으로 목이 비었다)은 받은 그대로 둔다.
 */
export function currentProposalCard(card: DeleteProposalCard): DeleteProposalCard {
  const stored = proposals.get(card.proposal_id);
  if (!stored) return card;
  const status = effectiveStatus(stored);
  return status === "pending"
    ? stored.card
    : { ...stored.card, status, result: stored.result, tabs: [] };
}

function out(proposalId: string, stored: StoredProposal): DeleteProposal {
  return {
    id: proposalId,
    status: effectiveStatus(stored),
    expires_at: stored.card.expires_at,
    result: stored.result,
  };
}

/** 후보 id → 종류. */
function candidates(card: DeleteProposalCard): Map<string, string> {
  return new Map(
    card.tabs.flatMap((tab: CardTab) => [
      ...tab.records.map((r) => [r.id, tab.kind] as const),
      ...tab.events.map((e) => [e.id, tab.kind] as const),
    ]),
  );
}

/** 제안한 보호자의 제안만 — 남의 제안은 없는 것과 같은 404 다. 목은 보호자가 한 명이라 아이로만 본다. */
function find(cid: string, pid: string): StoredProposal | null {
  const stored = proposals.get(pid);
  return stored && stored.childId === cid ? stored : null;
}

function closedError(stored: StoredProposal) {
  return effectiveStatus(stored) === "expired"
    ? apiError(409, "proposal_expired", "시간이 지나 삭제 확인이 닫혔어요")
    : apiError(409, "proposal_closed", "이미 끝난 삭제 확인이에요");
}

export const deleteProposalHandlers = [
  http.post(
    url("/children/:cid/delete-proposals/:pid/confirm"),
    withIdempotency(async ({ params, request }) => {
      await networkDelay();
      const body = (await request.json()) as ConfirmDeleteProposalRequest;
      if (
        !Array.isArray(body.ids) ||
        body.ids.length < 1 ||
        body.ids.length > CONFIRM_DELETE_MAX_IDS
      ) {
        return apiError(400, "validation_failed", "요청 형식이 올바르지 않아요");
      }
      const pid = String(params.pid);
      const stored = find(String(params.cid), pid);
      if (!stored) return apiError(404, "not_found", "삭제 확인을 찾지 못했어요");
      if (effectiveStatus(stored) !== "pending") return closedError(stored);

      const kindOf = candidates(stored.card);
      const ids = [...new Set(body.ids)];
      if (ids.some((id) => !kindOf.has(id))) {
        return apiError(422, "not_in_proposal", "삭제 확인에 없던 기록이 섞여 있어요");
      }
      const result: Record<string, number> = {};
      for (const id of ids) {
        const kind = kindOf.get(id)!;
        result[kind] = (result[kind] ?? 0) + 1;
      }
      stored.status = "deleted";
      stored.result = result;
      return HttpResponse.json({ proposal: out(pid, stored) } satisfies DeleteProposalResponse);
    }),
  ),

  // 키가 없다 — 지우지 않아서 다시 보내도 같은 응답이다.
  http.post(url("/children/:cid/delete-proposals/:pid/cancel"), async ({ params }) => {
    await networkDelay();
    const pid = String(params.pid);
    const stored = find(String(params.cid), pid);
    if (!stored) return apiError(404, "not_found", "삭제 확인을 찾지 못했어요");
    if (stored.status !== "cancelled") {
      if (effectiveStatus(stored) !== "pending") return closedError(stored);
      stored.status = "cancelled";
    }
    return HttpResponse.json({ proposal: out(pid, stored) } satisfies DeleteProposalResponse);
  }),
];
