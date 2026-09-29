import { http, HttpResponse } from "msw";

import type {
  ChildParent,
  ChildParentsResponse,
  ConsentHistoryEntry,
  ConsentsResponse,
  InviteResponse,
} from "@/lib/api/types";
import { PARENT_ID } from "../fixtures";
import { INVITE_CODE_LENGTH } from "@/lib/invite-code";
import { apiError, networkDelay, url } from "./helpers";

/**
 * 10 설정 — 동의 현황 · 함께 보는 보호자 · 초대.
 *
 * 🚨 **동의는 상태를 들고 있어야 한다.** 켜고 끄는 화면이라, 응답이 매번 같으면 무엇을 눌러도
 *    화면이 안 변해서 "동의/철회가 되는지" 를 확인할 수 없다. 그래서 여기만 목이 서버처럼
 *    기억한다 (`resetConsentState()` 를 테스트 `afterEach` 에 건다).
 *
 * 🚨 **철회도 행을 추가하는 것이지 지우는 게 아니다** (계약서 §04 append-only).
 *    `history` 에서 이전 행을 빼지 않는다 — 증빙이라 삭제 엔드포인트 자체가 없다.
 */

/** 가입을 마친 계정의 출발 상태. 선택 2건 중 아이 건강만 켜져 있다. */
function initialEffective(): Record<string, boolean> {
  return {
    service_terms: true,
    privacy_account: true,
    child_basic: true,
    child_health: true,
    location: false,
  };
}

let effective = initialEffective();
let history: ConsentHistoryEntry[] = [];

export function consentEffective(): Record<string, boolean> {
  return { ...effective };
}

/** `POST /consents` 가 부른다. 계정 스코프든 아이 스코프든 같은 표에 쌓인다. */
export function recordConsent(
  scope: string,
  action: "granted" | "withdrawn",
  policyVersion: string,
): ConsentHistoryEntry {
  const entry: ConsentHistoryEntry = {
    scope,
    action,
    policy_version: policyVersion,
    acted_at: new Date().toISOString(),
  };
  effective = { ...effective, [scope]: action === "granted" };
  // 최신이 위로 오게 쌓는다 — 화면이 받아 온 순서를 그대로 그린다.
  history = [entry, ...history];
  return entry;
}

export function resetConsentState(): void {
  effective = initialEffective();
  history = [];
}

/* ── 함께 보는 보호자 ─────────────────────────────────────────────────── */

/**
 * 🚨 **owner 는 목록에서 뺄 수 없다** (계약서 §07 · 409 `owner_required`).
 *    이관은 `child.owner_parent_id` 변경으로 처리하는 것이라 이 화면의 일이 아니다.
 */
function initialParents(): ChildParent[] {
  return [
    {
      parent_id: PARENT_ID,
      nickname: "지은",
      relation: "mother",
      role: "owner",
      connected_at: "2026-08-30T11:00:00+09:00",
    },
    {
      parent_id: "p2",
      nickname: "현우",
      relation: "father",
      role: "member",
      connected_at: "2026-09-02T20:10:00+09:00",
    },
  ];
}

let parents = initialParents();

export function resetParentState(): void {
  parents = initialParents();
}

export const settingsHandlers = [
  http.get(url("/consents"), async () => {
    await networkDelay();
    const body: ConsentsResponse = { effective: consentEffective(), history };
    return HttpResponse.json(body);
  }),

  http.get(url("/children/:cid/parents"), async () => {
    await networkDelay();
    const body: ChildParentsResponse = { parents };
    return HttpResponse.json(body);
  }),

  http.delete(url("/children/:cid/parents/:pid"), async ({ params }) => {
    await networkDelay();
    const pid = String(params.pid);
    const target = parents.find((p) => p.parent_id === pid);
    if (!target) return apiError(404, "not_found", "그 보호자를 찾지 못했어요");
    // 🚨 owner 는 연결을 끊는 대상이 아니다. 화면이 버튼을 안 그려도 서버가 다시 막는다.
    if (target.role === "owner") {
      return apiError(409, "owner_required", "아이를 만든 보호자는 연결을 끊을 수 없어요");
    }
    parents = parents.filter((p) => p.parent_id !== pid);
    return new HttpResponse(null, { status: 204 });
  }),

  /**
   * 🚨 **`relation` 없이도 발행된다.** 아이와 어떤 사이인지는 받는 쪽이 수락 화면에서 고르는
   *    값이라 화면이 안 보낸다 (#89). 계약서 §08 은 아직 발행 시 지정하는 것으로 적혀 있어서,
   *    보내오면 받아는 주되 없다고 막지 않는다 — 막으면 화면이 아예 초대를 못 한다.
   */
  http.post(url("/children/:cid/invites"), async () => {
    await networkDelay();
    // 코드는 서버가 만든다. 목이라 Crockford Base32 알파벳 안에서 8자만 맞춘다
    // (`docs/api/invite-v1.md` §4 — 서버는 정규화한 값을 해시로 저장한다).
    const code = randomInviteCode();
    const res: InviteResponse = {
      invite_code: code,
      expires_at: new Date(Date.now() + 24 * 60 * 60 * 1000).toISOString(),
    };
    return HttpResponse.json(res, { status: 201 });
  }),
];

/** 🚨 알파벳을 손으로 쓰지 않는다 — 화면의 정규화(`I`·`L`·`O` 치환)를 통과하는 값만 나와야 한다. */
function randomInviteCode(): string {
  const alphabet = "0123456789ABCDEFGHJKMNPQRSTVWXYZ";
  return Array.from(
    { length: INVITE_CODE_LENGTH },
    () => alphabet[Math.floor(Math.random() * alphabet.length)],
  ).join("");
}
