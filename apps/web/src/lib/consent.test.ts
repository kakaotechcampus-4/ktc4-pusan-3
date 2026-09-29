import { describe, expect, it } from "vitest";

import {
  consentChoices,
  consentPayload,
  consentTarget,
  isConsentChecked,
  policyHref,
  requiredConsentsChecked,
  toggleConsent,
  type ConsentChecked,
} from "@/lib/consent";
import { API_BASE_URL } from "@/lib/env";
import type { Policy } from "@/lib/api/types";

/**
 * 동의 체크가 **버전을 들고 있다**는 것이 이 파일이 거는 것 전부다 (#90).
 *
 * 🚨 boolean 으로 두면 약관이 바뀐 뒤 화면을 다시 받아도 **옛 글을 읽고 누른 체크가 새 글의
 *    동의로 넘어간다.** 화면에서는 눈에 안 띄고 서버 기록으로만 남는 종류의 사고라, 여기서 건다.
 */

function policy(scope: string, over: Partial<Policy> = {}): Policy {
  return {
    scope,
    version: "draft-1",
    label: `${scope} 라벨`,
    legal_basis: null,
    required: true,
    sensitive: false,
    html_path: `/policies/${scope}/draft-1`,
    ...over,
  };
}

describe("동의 체크는 버전과 함께 기록된다", () => {
  it("체크하면 그때 화면에 있던 버전이 남는다", () => {
    const terms = policy("service_terms");
    const checked = toggleConsent({}, terms, true);

    expect(checked.service_terms).toBe("draft-1");
    expect(isConsentChecked(terms, checked)).toBe(true);
  });

  it("🚨 약관이 바뀌면 그 항목만 체크가 풀린다", () => {
    const terms = policy("service_terms");
    const account = policy("privacy_account");
    let checked: ConsentChecked = toggleConsent({}, terms, true);
    checked = toggleConsent(checked, account, true);

    const bumped = policy("service_terms", { version: "draft-2" });

    expect(isConsentChecked(bumped, checked)).toBe(false);
    // 안 바뀐 항목은 그대로다 — 다시 읽게 하는 것은 바뀐 글뿐이다.
    expect(isConsentChecked(account, checked)).toBe(true);
  });

  it("체크를 풀면 버전도 지워진다", () => {
    const terms = policy("service_terms");
    const checked = toggleConsent(toggleConsent({}, terms, true), terms, false);

    expect(checked.service_terms).toBeUndefined();
  });
});

describe("제출을 막는 것은 required 뿐이다", () => {
  const choices = consentChoices([
    policy("service_terms"),
    policy("location", { required: false }),
  ]);

  it("선택을 안 골라도 통과한다", () => {
    const checked = toggleConsent({}, choices[0]!.policy, true);
    expect(requiredConsentsChecked(choices, checked)).toBe(true);
  });

  it("필수가 비면 막힌다", () => {
    const checked = toggleConsent({}, choices[1]!.policy, true);
    expect(requiredConsentsChecked(choices, checked)).toBe(false);
  });

  it("🚨 필수가 낡은 버전으로 체크돼 있으면 막힌다", () => {
    const checked: ConsentChecked = { service_terms: "draft-0" };
    expect(requiredConsentsChecked(choices, checked)).toBe(false);
  });
});

describe("보내는 것은 고른 것뿐이다", () => {
  it("체크한 항목만, 지금 버전으로 실린다", () => {
    const choices = consentChoices([
      policy("service_terms"),
      policy("privacy_account"),
      policy("location", { required: false }),
    ]);
    const checked = toggleConsent(
      toggleConsent({}, choices[0]!.policy, true),
      choices[2]!.policy,
      true,
    );

    expect(consentPayload(choices, checked)).toEqual([
      { scope: "service_terms", policy_version: "draft-1" },
      { scope: "location", policy_version: "draft-1" },
    ]);
  });

  it("낡은 체크는 실리지 않는다 — 화면이 보여준 글과 다른 것에 동의로 남지 않게", () => {
    const choices = consentChoices([policy("service_terms", { version: "draft-2" })]);
    expect(consentPayload(choices, { service_terms: "draft-1" })).toEqual([]);
  });
});

describe("어느 화면이 묻는가", () => {
  it("아이 스코프 둘만 아이 화면이다", () => {
    expect(consentTarget("child_basic")).toBe("child");
    expect(consentTarget("child_health")).toBe("child");
    expect(consentTarget("location")).toBe("account");
  });

  /**
   * 🚨 **모르는 scope 는 계정 쪽이다.** 아이 쪽에 넣으면 새로 생긴 필수 계정 동의를 아무도
   *    못 켜서 가입 자체가 막힌다 (`lib/consent.ts` 의 `CHILD_CONSENT_SCOPES` 주석).
   */
  it("모르는 scope 는 가입 화면에 선다", () => {
    const choices = consentChoices([policy("some_new_scope")], "account");
    expect(choices).toHaveLength(1);
    expect(choices[0]!.copy).toBeUndefined();
  });

  it("응답 순서를 그대로 둔다 — 정렬하지 않는다", () => {
    const scopes = consentChoices([policy("child_health"), policy("child_basic")], "child").map(
      (c) => c.policy.scope,
    );
    expect(scopes).toEqual(["child_health", "child_basic"]);
  });
});

describe("전문 주소", () => {
  it("기준 주소 뒤에 그대로 붙인다", () => {
    expect(policyHref(policy("location"))).toBe(`${API_BASE_URL}/policies/location/draft-1`);
  });

  it("🚨 정본이 없으면 null 이다 — 화면은 버튼을 숨긴다", () => {
    expect(policyHref(policy("child_basic", { html_path: null }))).toBeNull();
  });
});
