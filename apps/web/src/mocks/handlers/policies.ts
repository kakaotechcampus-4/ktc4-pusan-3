import { http, HttpResponse } from "msw";

import type { Policy } from "@/lib/api/types";
import { currentScenario } from "../scenario";
import { networkDelay, url } from "./helpers";

/**
 * `GET /policies` — 동의 화면이 그릴 약관 (#91 · #172). **무인증이다.**
 *
 * 🚨 **실서버가 지금 주는 것과 같은 모양으로 둔다.** 버전 · 순서 · `html_path` 가 `null` 인
 *    자리까지 맞춘다 (`apps/api/alembic/versions/…register_policy_draft_1.py` ·
 *    `app/domains/policy/catalog.py`). 목이 더 친절하면 화면은 목에서만 동작한다 — #90 이
 *    잡으려던 회귀가 정확히 그거였다 (프론트 상수 `2026-09-01` 이 DB 에 없는데 목은 통과시켰다).
 *
 * 🚨 **본문(`content`)을 내려보내지 않는다.** 전문은 `html_path` 의 정본 페이지다.
 *    그 페이지 자체는 목이 흉내 내지 않는다 — 새 창으로 여는 **전체 페이지 이동**이라
 *    서비스 워커가 잡지 못한다 (`handlers/auth.ts` 의 OAuth 왕복과 같은 이유).
 *    목으로 화면을 볼 때 전문 보기는 실제 API 주소로 나간다.
 */
const BASE: Policy[] = [
  {
    scope: "service_terms",
    version: "draft-1",
    label: "서비스 이용약관",
    legal_basis: null,
    required: true,
    sensitive: false,
    target: "account",
    html_path: "/policies/service_terms/draft-1",
  },
  {
    scope: "privacy_account",
    version: "draft-1",
    label: "개인정보 수집·이용 (보호자 본인)",
    legal_basis: null,
    required: true,
    sensitive: false,
    target: "account",
    html_path: "/policies/privacy_account/draft-1",
  },
  {
    // 🚨 **선택이다.** 이 한 건 때문에 "필수 = 목록 전체" 가 깨졌다 (#173).
    scope: "location",
    version: "draft-1",
    label: "위치정보 수집·이용",
    legal_basis: "위치정보의 보호 및 이용 등에 관한 법률 제19조 (개인위치정보의 이용·제공)",
    required: false,
    sensitive: false,
    target: "account",
    html_path: "/policies/location/draft-1",
  },
  {
    // 🚨 아이 동의 둘은 아직 `draft-0` 이고 **정본 HTML 이 없다** — 화면은 전문 보기를 숨긴다.
    scope: "child_basic",
    version: "draft-0",
    label: "개인정보 수집·이용 (아이 기본정보)",
    legal_basis: "개인정보보호법 제22조의2 (만 14세 미만 아동의 법정대리인 동의)",
    required: true,
    sensitive: false,
    target: "child",
    html_path: null,
  },
  {
    scope: "child_health",
    version: "draft-0",
    label: "민감정보 처리 (아이 건강·알레르기)",
    legal_basis: "개인정보보호법 제23조 (민감정보의 처리, 별도 동의)",
    required: true,
    sensitive: true,
    target: "child",
    html_path: null,
  },
];

/**
 * `policy_bumped` 시나리오에서 버전이 올라간 scope.
 *
 * 🚨 **정적으로 올려 둘 수 없는 상태다.** "화면이 낡았다" 는 *받아 간 뒤에 바뀐 것*이라,
 *    처음부터 새 버전을 내려주면 화면은 그 새 버전을 보내 그냥 성공한다. 그래서 **첫 제출이
 *    바꾼다** — 그 순간이 실제로 재현하려는 순간이다.
 */
const bumped = new Set<string>();

/** 낡은 화면이 겪는 것: 제출 직전에 계정 약관 하나가 새 판으로 바뀐다. */
const BUMPED_SCOPE = "service_terms";

function bumpedVersion(policy: Policy): Policy {
  const version = `${policy.version}-b`;
  return {
    ...policy,
    version,
    html_path: policy.html_path === null ? null : `/policies/${policy.scope}/${version}`,
  };
}

/** 지금 유효한 약관. 화면이 받아 가는 것과 아래 검사가 **같은 표**를 본다. */
export function currentPolicies(): Policy[] {
  return BASE.map((policy) => (bumped.has(policy.scope) ? bumpedVersion(policy) : policy));
}

/**
 * 보낸 동의가 **지금 유효한 버전**인가 (실서버 `POST /auth/{provider}/signup` 과 같은 검사).
 *
 * 🚨 목이라고 아무 버전이나 받지 않는다. 이 검사가 없어서 프론트가 상수 버전을 보내는 것을
 *    아무도 못 봤다 (#90 본문). 어긋나면 어느 scope 가 왜 막혔는지까지 돌려준다.
 */
export function invalidPolicyVersion(
  consents: ReadonlyArray<{ scope: string; policy_version?: string }>,
): { scope: string; policy_version: string } | null {
  if (currentScenario() === "policy_bumped") bumped.add(BUMPED_SCOPE);

  const active = new Map(currentPolicies().map((policy) => [policy.scope, policy.version]));
  for (const consent of consents) {
    // 모르는 scope 는 서버도 그냥 지나친다 (계정 가입은 계정 scope 만 본다).
    const version = active.get(consent.scope);
    if (version === undefined) continue;
    if (consent.policy_version !== version) {
      return { scope: consent.scope, policy_version: consent.policy_version ?? "" };
    }
  }
  return null;
}

export function resetPolicyState(): void {
  bumped.clear();
}

export const policyHandlers = [
  http.get(url("/policies"), async () => {
    await networkDelay();
    return HttpResponse.json(currentPolicies());
  }),
];
