import { http, HttpResponse } from "msw";

import type { InviteAcceptResponse, InvitePreviewResponse, Relation } from "@/lib/api/types";

import { isInviteCodeComplete, normalizeInviteCode } from "@/lib/invite-code";
import { CHILD_ID, me } from "../fixtures";
import { apiError, networkDelay, url } from "./helpers";
import { currentMe, joinChild } from "./membership";

/**
 * 초대 확인 · 수락. ⚠️ **둘 다 계약 확정 전이다** (#96) — 계약서 §05 는 링크
 * (`/invites/{token}/accept`)뿐이고 `GET /invites/{code}` 도 응답 모양도 없다
 * (`lib/invite-code.ts` · `InvitePreviewResponse`).
 *
 * 🚨 **실패를 시나리오로 만들지 않았다.** 목 시나리오는 *실서버로 만들기 어려운 상태* 를 위한
 *    것인데(apps/web/CLAUDE.md §7), 만료·재사용·중복 등록은 **입력값으로 갈리는 것**이라
 *    실서버에서도 그냥 일어난다. 08 사진의 두 lane 을 시나리오로 두지 않은 것과 같은 이유다.
 *    아래 코드를 넣으면 그 실패를 그대로 볼 수 있다.
 */
const FAILING_CODES: Record<string, () => Response> = {
  // 🚨 Crockford Base32 라 `I` `L` `O` `U` 가 없다 — 여기 코드도 그 알파벳 안에 있어야
  //    화면에서 정규화를 거친 뒤에도 같은 값으로 남는다.
  MKWASTED: () => apiError(409, "invite_used", "이미 사용된 코드예요"),
  MKPAST12: () => apiError(410, "invite_expired", "코드 기한이 지났어요"),
  MKTAKEN2: () => apiError(409, "child_already_exists", "이미 등록한 아이가 있어요"),
};

/**
 * 🚨 **시도 제한이 코드 방식의 전제다** (8자 = 40비트). 서버가 이걸 안 하면 화면만으로는
 *    못 막으니, 목이 먼저 그 동작을 세워 둔다 — 화면이 `429` 문구를 갖고 있는지 건다.
 *
 * 🚨 **확인과 수락이 같은 통을 쓴다.** 확인 쪽이 **더 좋은 추측 도구**라서다 — 코드를
 *    소비하지 않고 계정 상태도 안 보므로, 여기만 열려 있으면 제한이 있으나 마나다.
 */
const ATTEMPT_LIMIT = 5;
let failedAttempts = 0;

export function resetInviteState(): void {
  failedAttempts = 0;
}

/** 형식·존재·상태 검사. 통과하면 `null`, 막히면 그 응답. 🚨 실패는 시도 제한에 쌓인다. */
function rejectCode(code: string): Response | null {
  if (failedAttempts >= ATTEMPT_LIMIT) {
    return apiError(429, "too_many_attempts", "여러 번 틀렸어요. 잠시 후 다시 시도해 주세요");
  }
  const fail = (response: Response) => {
    failedAttempts += 1;
    return response;
  };

  if (!isInviteCodeComplete(code)) {
    return fail(apiError(404, "invite_not_found", "이 코드를 찾을 수 없어요"));
  }
  const failing = FAILING_CODES[code];
  if (failing) return fail(failing());

  // 🚨 아이는 보호자당 한 명이다 (#96). 화면이 막아도 서버가 다시 막는다.
  //    확인 단계에서 미리 막는다 — 아이 프로필까지 보여주고 나서 "안 된다" 고 하면,
  //    연결되지 않을 아이의 별명·나이를 보여준 것이 된다.
  if (currentMe().children.length > 0) {
    return fail(apiError(409, "child_already_exists", "이미 등록한 아이가 있어요"));
  }
  return null;
}

export const inviteHandlers = [
  /**
   * 🚨 **이 호출은 코드를 쓰지 않는다.** 확인 화면에서 그만둔 사람의 코드가 소비되면,
   *    한 번만 쓸 수 있는 코드라 다시 받아야 한다. 소비는 `accept` 하나만 한다.
   * 🚨 **아이의 건강·알레르기를 내리지 않는다** — 아직 `parent_child` 행이 없는 사람이고,
   *    코드만 알면 누구나 부를 수 있는 창구다 (최상위 §2 개인정보 · 최소 수집).
   */
  http.get(url("/invites/:code"), async ({ params }) => {
    await networkDelay();
    const rejected = rejectCode(normalizeInviteCode(String(params.code)));
    if (rejected) return rejected;

    const res: InvitePreviewResponse = {
      child: { nickname: "민준", age_display: "만 4세" },
      invited_by: { nickname: me.nickname },
      expires_at: new Date(Date.now() + 24 * 60 * 60 * 1000).toISOString(),
    };
    return HttpResponse.json(res);
  }),

  http.post(url("/invites/:code/accept"), async ({ params, request }) => {
    await networkDelay();
    const rejected = rejectCode(normalizeInviteCode(String(params.code)));
    if (rejected) return rejected;

    // 🚨 **관계는 받는 쪽이 보낸다.** 안 보내면 비워 둔다 — 목이 기본값을 채우면
    //    "안 골라도 뭔가 들어간다" 가 되어 화면이 그 필드를 안 보내도 모른다.
    const body = (await request.json().catch(() => ({}))) as { relation?: Relation };

    // 초대받은 보호자는 owner 가 아니다. 나머지 화면이 그대로 돌게 같은 아이에 붙인다.
    joinChild({
      child_id: CHILD_ID,
      nickname: "민준",
      age_display: "만 4세",
      relation: body.relation ?? "other",
      role: "member",
      consent_required: [],
    });

    const res: InviteAcceptResponse = {
      child_id: CHILD_ID,
      nickname: "민준",
      age_display: "만 4세",
      role: "member",
    };
    return HttpResponse.json(res);
  }),
];
