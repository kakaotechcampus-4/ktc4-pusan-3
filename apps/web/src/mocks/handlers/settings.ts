import { http, HttpResponse } from "msw";

import type {
  ChildParent,
  ChildParentsResponse,
  ConsentHistoryEntry,
  ConsentRequest,
  ConsentResponse,
  ConsentsResponse,
  InviteResponse,
  NotificationSettings,
} from "@/lib/api/types";
import { CHILD_ID, PARENT_ID } from "../fixtures";
import { currentScenario } from "../scenario";
import { INVITE_CODE_LENGTH } from "@/lib/invite-code";
import { apiError, networkDelay, url } from "./helpers";
import { currentMe, leaveChild } from "./membership";
import {
  currentPolicies,
  initialPolicyVersion,
  invalidPolicyVersion,
  isRegisteredVersion,
} from "./policies";

/**
 * 10 설정 — 동의 현황 · 함께 보는 보호자 · 초대.
 *
 * 🚨 **동의는 상태를 들고 있어야 한다.** 켜고 끄는 화면이라, 응답이 매번 같으면 무엇을 눌러도
 *    화면이 안 변해서 "동의/철회가 되는지" 를 확인할 수 없다. 그래서 여기만 목이 서버처럼
 *    기억한다 (`resetConsentState()` 를 테스트 `afterEach` 에 건다).
 *
 * 🚨 **철회도 행을 추가하는 것이지 지우는 게 아니다** (계약서 §04 append-only).
 *    이력에서 이전 행을 빼지 않는다 — 증빙이라 삭제 엔드포인트 자체가 없다.
 *
 * 🚨 **실서버와 같은 구조로 든다** (PR #355). 계정 동의는 보호자 본인의 것, 아이 동의는 그 아이의
 *    것이라 대상이 다르다. 지금 상태(`effective`)는 따로 들지 않고 **이력의 마지막 행**에서 계산한다
 *    — 두 벌로 들면 "켰는데 꺼져 있다" 가 목에서 생긴다.
 */

/** 목의 동의 1행. `child_id` 가 `null` 이면 이 보호자의 계정 동의다. */
interface ConsentRow extends ConsentHistoryEntry {
  id: string;
  child_id: string | null;
}

/**
 * 가입과 아이 등록 때 남은 행 — 실서버도 이 행들을 이력에 넣어 돌려준다.
 *
 * 🚨 **아이 동의는 그 아이의 것이라 늘 있다.** 초대로 들어온 보호자도 owner 가 등록할 때 남긴 행을
 *    본다. 계정 동의는 `consent` 시나리오(이제 가입하는 보호자)에서만 없다 — 가입이 쌓는다.
 *    🚨 선택 동의(`location`)는 넣지 않는다. 가입을 마친 계정의 출발 상태는 위치 꺼짐이다.
 */
function seededRows(): ConsentRow[] {
  const row = (scope: string, childId: string | null, actedAt: string): ConsentRow => ({
    id: `cs_seed_${scope}`,
    child_id: childId,
    scope,
    action: "granted",
    policy_version: initialPolicyVersion(scope),
    acted_at: actedAt,
  });
  const child = [
    row("child_health", CHILD_ID, "2026-08-30T11:00:00+09:00"),
    row("child_basic", CHILD_ID, "2026-08-30T11:00:00+09:00"),
  ];
  if (currentScenario() === "consent") return child;
  return [
    ...child,
    row("privacy_account", null, "2026-08-30T10:50:00+09:00"),
    row("service_terms", null, "2026-08-30T10:50:00+09:00"),
  ];
}

/** 이번 프로세스에서 새로 쌓인 행. 최신이 앞이다. */
let recorded: ConsentRow[] = [];
let seq = 0;

/** 전체 이력, 최신순. 쌓인 행은 언제나 출발 행보다 나중이다. */
function allRows(): ConsentRow[] {
  return [...recorded, ...seededRows()];
}

/** 이 대상의 이 동의의 마지막 행. */
function latestRow(scope: string, childId: string | null): ConsentRow | undefined {
  return allRows().find((r) => r.scope === scope && r.child_id === childId);
}

/** 서버가 동의를 어느 대상에 묶는가 (`policy.target`). 모르는 scope 는 `undefined`. */
function targetOf(scope: string): "account" | "child" | undefined {
  return currentPolicies().find((p) => p.scope === scope)?.target;
}

/**
 * scope → 지금 동의했는가. 실서버 `_effective` 와 같은 범위다 — 계정 동의는 늘, 아이 동의는
 * `childId` 를 줬을 때만 들어간다.
 */
function consentEffective(childId: string | null): Record<string, boolean> {
  const effective: Record<string, boolean> = {};
  for (const policy of currentPolicies()) {
    if (policy.target === "child" && childId === null) continue;
    const owner = policy.target === "child" ? childId : null;
    effective[policy.scope] = latestRow(policy.scope, owner)?.action === "granted";
  }
  return effective;
}

/** 내 계정 동의 이력과, `childId` 를 주면 그 아이의 이력. 최신순. */
function consentHistory(childId: string | null): ConsentHistoryEntry[] {
  return allRows()
    .filter((r) => r.child_id === null || (childId !== null && r.child_id === childId))
    .map(({ scope, action, policy_version, acted_at }) => ({
      scope,
      action,
      policy_version,
      acted_at,
    }));
}

/**
 * 동의 1행을 남긴다 — 가입 · 아이 등록 · 10 설정이 같은 함수를 쓴다 (실서버 `record_consent`).
 *
 * 🚨 **같은 상태면 행을 넣지 않는다.** 철회는 동작만, 동의는 **약관 버전까지** 비교한다 — 이미
 *    동의한 보호자가 새 판에 다시 동의한 것은 새 증빙이라 남긴다 (PR #355 리뷰). 같은 상태면
 *    `created: false` 와 함께 마지막 행을 돌려준다.
 */
export function recordConsent(
  childId: string | null,
  scope: string,
  action: "granted" | "withdrawn",
  policyVersion: string,
): { row: ConsentRow; created: boolean } {
  const latest = latestRow(scope, childId);
  if (
    latest &&
    latest.action === action &&
    (action === "withdrawn" || latest.policy_version === policyVersion)
  ) {
    return { row: latest, created: false };
  }
  seq += 1;
  const row: ConsentRow = {
    id: `cs_${seq}`,
    child_id: childId,
    scope,
    action,
    policy_version: policyVersion,
    acted_at: new Date().toISOString(),
  };
  recorded = [row, ...recorded];
  return { row, created: true };
}

export function resetConsentState(): void {
  recorded = [];
  seq = 0;
}

/** 그 아이에 연결된 보호자인가 — 아니면 403 `child_access_denied` (실서버 `_ensure_child`). */
function isLinkedChild(childId: string): boolean {
  return currentMe().children.some((child) => child.child_id === childId);
}

/* ── 함께 보는 보호자 ─────────────────────────────────────────────────── */

/**
 * 🚨 **owner 는 목록에서 뺄 수 없다** (계약서 §07 · 409 `owner_required`).
 *    이관은 `child.owner_parent_id` 변경으로 처리하는 것이라 이 화면의 일이 아니다.
 *
 * 🚨 **이름이 빈 보호자가 한 줄 있다** (#336 · `nickname: null`). 화면이 관계 이름으로 부르는지 여기서 본다.
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
    {
      parent_id: "p3",
      nickname: null,
      relation: "sitter",
      role: "member",
      connected_at: "2026-09-10T09:30:00+09:00",
    },
  ];
}

/**
 * 초대한 사람(= member 시점 목록의 owner). 초대 미리보기(`GET /invites/{code}`)도 이 이름을 쓴다.
 *
 * 🚨 **`me.nickname` 을 쓰지 않는다.** `consent` 시나리오에서 초대를 받는 사람도 `me`(지은)라서,
 *    미리보기가 `me` 를 쓰면 "지은이 지은을 초대했다" 가 되고 설정의 owner 와도 어긋난다.
 */
export const INVITER_NICKNAME = "현우";

/**
 * 초대로 들어온 member 가 보는 목록. `consent` 시나리오에서 초대를 수락하면 이쪽이다.
 *
 * 🚨 **보는 사람(`PARENT_ID`)이 member 로 서고 owner 는 다른 사람이다.** owner 시점 목록을
 *    그대로 주면 보는 사람이 owner 줄에 있어서 member 화면(나가기 버튼)을 열어 볼 수 없다.
 */
function initialMemberView(relation: ChildParent["relation"]): ChildParent[] {
  return [
    {
      parent_id: "p2",
      nickname: INVITER_NICKNAME,
      relation: "father",
      role: "owner",
      connected_at: "2026-08-30T11:00:00+09:00",
    },
    {
      parent_id: PARENT_ID,
      nickname: currentMe().nickname,
      relation,
      role: "member",
      connected_at: new Date().toISOString(),
    },
    {
      parent_id: "p3",
      nickname: null,
      relation: "sitter",
      role: "member",
      connected_at: "2026-09-10T09:30:00+09:00",
    },
  ];
}

let parents = initialParents();
let memberView: ChildParent[] | null = null;

export function resetParentState(): void {
  parents = initialParents();
  memberView = null;
}

/** 보는 사람의 role 에 맞는 목록. 연결이 없으면 `null` — 서버의 `AccessibleChild` 403 이다. */
function parentsFor(cid: string): { role: "owner" | "member"; rows: ChildParent[] } | null {
  const link = currentMe().children.find((child) => child.child_id === cid);
  if (!link) return null;
  if (link.role === "owner") return { role: "owner", rows: parents };
  memberView ??= initialMemberView(link.relation as ChildParent["relation"]);
  return { role: "member", rows: memberView };
}

function setParentsFor(role: "owner" | "member", rows: ChildParent[]): void {
  if (role === "owner") parents = rows;
  else memberView = rows;
}

/**
 * 알림 스위치 (#609 · 서버 #605). 🚨 프로세스 수명만큼 산다 — `resetNotificationSettings()` 로 되돌린다.
 *
 * 처음 값은 서버 `DEFAULT_SETTING` 과 같다 (둘 다 켜짐). ⚠️ 그 기본값은 PM 결정 전 자리값이다 (#605).
 */
const DEFAULT_NOTIFICATION_SETTINGS: NotificationSettings = {
  schedule_enabled: true,
  medication_enabled: true,
};
let notificationSettings = DEFAULT_NOTIFICATION_SETTINGS;

export function resetNotificationSettings(): void {
  notificationSettings = DEFAULT_NOTIFICATION_SETTINGS;
}

export const settingsHandlers = [
  http.get(url("/me/notification-settings"), async () => {
    await networkDelay();
    return HttpResponse.json(notificationSettings);
  }),

  /**
   * 🚨 **두 칸을 다 받는다.** 하나라도 빠지거나 다른 칸이 끼면 서버 바디 검증(`extra="forbid"`)과 같은 400 이다.
   * ⚠️ **값의 모양은 보지 않는다.** 서버 스키마는 pydantic 기본(lax) 모드라 `"off"` · `1` 같은 값도
   *    불리언으로 바꿔 받는다 — 목이 그걸 400 으로 막으면 서버에 없는 거절을 지어낸 것이다.
   *    화면은 불리언만 보낸다.
   * 🚨 `withIdempotency` 로 감싸지 않는다 — 최종 상태를 보내는 요청이라 몇 번 보내도 결과가 같다.
   */
  http.put(url("/me/notification-settings"), async ({ request }) => {
    await networkDelay();
    const body = (await request.json()) as Partial<NotificationSettings>;
    const fields = (["schedule_enabled", "medication_enabled"] as const).filter(
      (field) => !(field in body),
    );
    if (fields.length > 0 || Object.keys(body).length !== 2) {
      return apiError(400, "validation_failed", "요청 형식이 올바르지 않아요", { fields });
    }
    notificationSettings = body as NotificationSettings;
    return HttpResponse.json(notificationSettings);
  }),

  /**
   * 🚨 `child_id` 가 없으면 **계정 동의만** 준다. 아이 동의는 그 아이를 가리켜야 읽을 수 있고,
   *    남의 아이면 403 이다 (실서버 `GET /consents` · PR #355).
   */
  http.get(url("/consents"), async ({ request }) => {
    await networkDelay();
    const childId = new URL(request.url).searchParams.get("child_id");
    if (childId !== null && !isLinkedChild(childId)) {
      return apiError(403, "child_access_denied", "이 아이에 접근할 수 없어요");
    }
    const body: ConsentsResponse = {
      effective: consentEffective(childId),
      history: consentHistory(childId),
    };
    return HttpResponse.json(body);
  }),

  /**
   * 동의 1건을 켜거나 끈다. append-only — 철회도 withdrawn 행을 **추가**한다.
   *
   * 🚨 **가입 흐름은 이 경로를 쓰지 않는다.** 계정 동의는 `/signup` 바디로, 아이 동의는
   *    `POST /children` 바디로 간다 (#96). 여기로 오는 것은 10 설정에서 켜고 끄는 경우뿐이다.
   *
   * 🚨 **실서버와 같은 순서로 막고, 하나라도 걸리면 아무것도 쌓지 않는다** (PR #355).
   *    ① 대상(`child_id` 유무) → ② 필수 끄기 → ③ 남의 아이 → ④ 법정대리인 확인 → ⑤ 버전.
   *    순서가 다르면 같은 요청에 목과 서버가 다른 코드를 줘서, 화면이 목에서만 맞는 문구를 고른다.
   */
  http.post(url("/consents"), async ({ request }) => {
    await networkDelay();
    const body = (await request.json()) as Partial<ConsentRequest>;
    const target = typeof body.scope === "string" ? targetOf(body.scope) : undefined;
    if (
      target === undefined ||
      (body.action !== "granted" && body.action !== "withdrawn") ||
      !body.policy_version
    ) {
      return apiError(400, "validation_failed", "요청 형식이 올바르지 않아요");
    }
    const scope = body.scope as string;
    const action = body.action;
    const childId = body.child_id ?? null;

    // ① 계정 동의는 보호자 본인, 아이 동의는 그 아이 — 대상은 정확히 하나다.
    if ((target === "account") === (childId !== null)) {
      return apiError(400, "validation_failed", "동의 대상을 다시 확인해 주세요", {
        fields: ["child_id"],
      });
    }
    // ② 필수 동의를 끄는 것은 곧 탈퇴다. 무엇이 필수인지는 `GET /policies` 와 같은 값이다.
    const required = currentPolicies().find((p) => p.scope === scope)?.required === true;
    if (action === "withdrawn" && required) {
      return apiError(
        400,
        "validation_failed",
        "필수 동의는 철회할 수 없어요. 탈퇴로만 끝낼 수 있어요",
        { fields: ["scope"] },
      );
    }
    if (childId !== null) {
      // ③
      if (!isLinkedChild(childId)) {
        return apiError(403, "child_access_denied", "이 아이에 접근할 수 없어요");
      }
      // ④ 동의와 법정대리인 확인은 별개 의무다 — 아이 등록과 같다. 🚨 deeplink 가 없다.
      if (action === "granted" && body.guardian_attested !== true) {
        return apiError(403, "consent_required", "법정대리인 확인이 필요해요", {
          scopes: [scope],
        });
      }
    }
    // ⑤ 켜기는 지금 보여 주는 버전만, 끄기는 등록된 버전이면 받는다 — 약관이 바뀌었다고
    //    끄는 길이 막히면 안 된다 (위치 약관 제10조 "언제든").
    const versionOk =
      action === "granted"
        ? invalidPolicyVersion([{ scope, policy_version: body.policy_version }]) === null
        : isRegisteredVersion(scope, body.policy_version);
    if (!versionOk) {
      return apiError(400, "policy_version_invalid", "동의 화면을 다시 불러와 주세요", {
        scope,
        policy_version: body.policy_version,
      });
    }

    const { row } = recordConsent(childId, scope, action, body.policy_version);
    const res: ConsentResponse = {
      consent: {
        id: row.id,
        scope: row.scope,
        action: row.action,
        policy_version: row.policy_version,
        acted_at: row.acted_at,
      },
      // 🚨 계정 동의면 계정 동의만 온다 — GET ?child_id= 의 표와 키 수가 다르다.
      effective: consentEffective(childId),
    };
    return HttpResponse.json(res, { status: 201 });
  }),

  http.get(url("/children/:cid/parents"), async ({ params }) => {
    await networkDelay();
    const view = parentsFor(String(params.cid));
    if (!view) return apiError(403, "child_access_denied", "이 아이에 접근할 수 없어요");
    const body: ChildParentsResponse = { parents: view.rows };
    return HttpResponse.json(body);
  }),

  /**
   * 누가 누구를 끊나 (#336 · 재형님 결정 10-06). owner 는 member 누구든, member 는 자기 자신만.
   *
   * 🚨 **스스로 나가면 `/me` 에서도 빠진다** (`leaveChild`). 다시 보내면 연결이 없어서
   *    아이 접근 확인에서 403 `child_access_denied` 다 — 서버와 같다.
   */
  http.delete(url("/children/:cid/parents/:pid"), async ({ params }) => {
    await networkDelay();
    const cid = String(params.cid);
    const pid = String(params.pid);
    const view = parentsFor(cid);
    if (!view) return apiError(403, "child_access_denied", "이 아이에 접근할 수 없어요");

    const target = view.rows.find((p) => p.parent_id === pid);
    if (!target) return apiError(404, "not_found", "그 보호자를 찾지 못했어요");
    // 🚨 owner 는 연결을 끊는 대상이 아니다. 화면이 버튼을 안 그려도 서버가 다시 막는다.
    if (target.role === "owner") {
      return apiError(409, "owner_required", "아이를 만든 보호자는 연결을 끊을 수 없어요");
    }
    // 🚨 member 는 자기 자신만 끊는다. 화면은 남의 줄에 버튼을 안 그리지만 서버가 다시 막는다.
    if (view.role === "member" && pid !== currentMe().id) {
      return apiError(
        403,
        "owner_only",
        "아이를 등록한 보호자만 다른 보호자의 연결을 끊을 수 있어요",
      );
    }

    if (pid === currentMe().id) {
      // 다시 초대받아 들어오면 처음 목록부터 본다.
      leaveChild(cid);
      memberView = null;
    } else {
      setParentsFor(
        view.role,
        view.rows.filter((p) => p.parent_id !== pid),
      );
    }
    return new HttpResponse(null, { status: 204 });
  }),

  /**
   * 🚨 **`relation` 없이도 발행된다.** 아이와 어떤 사이인지는 받는 쪽이 수락 화면에서 고르는
   *    값이라 화면이 안 보낸다 (#89). 계약서 §08 은 아직 발행 시 지정하는 것으로 적혀 있어서,
   *    보내오면 받아는 주되 없다고 막지 않는다 — 막으면 화면이 아예 초대를 못 한다.
   * 🚨 **owner 만 발행한다** (#198). 연결 없음은 `403 child_access_denied`, 연결된 member 는
   *    `403 owner_only` — 화면은 member 에게 버튼을 안 그리지만 서버가 다시 막는다.
   *    member 계정은 `consent` 시나리오에서 초대를 수락하면 만들어진다.
   */
  http.post(url("/children/:cid/invites"), async ({ params }) => {
    await networkDelay();
    const link = currentMe().children.find((child) => child.child_id === String(params.cid));
    if (!link) return apiError(403, "child_access_denied", "이 아이에 접근할 수 없어요");
    if (link.role !== "owner") {
      return apiError(403, "owner_only", "아이를 등록한 보호자만 초대할 수 있어요");
    }
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
