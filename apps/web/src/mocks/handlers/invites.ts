import { http, HttpResponse } from "msw";

import type { InviteAcceptResponse } from "@/lib/api/types";

import { CHILD_ID } from "../fixtures";
import { normalizeInviteCode, isInviteCodeComplete } from "@/lib/invite-code";
import { apiError, networkDelay, url } from "./helpers";
import { currentMe, joinChild } from "./membership";

/**
 * 초대 수락. ⚠️ **계약서 §05 는 링크(`/invites/{token}/accept`)이고 응답 모양이 없다** —
 * 코드 방식과 응답 모양 둘 다 #96 에서 제안 중이다 (`lib/invite-code.ts` · `InviteAcceptResponse`).
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
 */
const ATTEMPT_LIMIT = 5;
let failedAttempts = 0;

export function resetInviteState(): void {
  failedAttempts = 0;
}

export const inviteHandlers = [
  http.post(url("/invites/:code/accept"), async ({ params }) => {
    await networkDelay();
    const code = normalizeInviteCode(String(params.code));

    if (failedAttempts >= ATTEMPT_LIMIT) {
      return apiError(429, "too_many_attempts", "여러 번 틀렸어요. 잠시 후 다시 시도해 주세요");
    }

    function fail(response: Response): Response {
      failedAttempts += 1;
      return response;
    }

    if (!isInviteCodeComplete(code)) {
      return fail(apiError(404, "invite_not_found", "이 코드를 찾을 수 없어요"));
    }
    const failing = FAILING_CODES[code];
    if (failing) return fail(failing());

    // 🚨 아이는 보호자당 한 명이다 (#96). 화면이 막아도 서버가 다시 막는다.
    if (currentMe().children.length > 0) {
      return fail(apiError(409, "child_already_exists", "이미 등록한 아이가 있어요"));
    }

    // 초대받은 보호자는 owner 가 아니다. 나머지 화면이 그대로 돌게 같은 아이에 붙인다.
    joinChild({
      child_id: CHILD_ID,
      nickname: "민준",
      age_display: "만 4세",
      relation: "father",
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
