/**
 * 동의 스코프 정본. 화면(가입 동의 · 10 설정)이 같은 목록을 두 벌 갖지 않게 여기 모은다.
 *
 * 근거: 계약서 §04 `POST /consents` · docs/api/auth-kakao-v1.md §3-5 · 개인정보보호법.
 */

/** 서버가 이 값으로 어느 버전에 동의했는지 기록한다. 약관을 고치면 여기부터 올린다. */
export const CONSENT_POLICY_VERSION = "2026-09-01";

export const CONSENT_SCOPES = [
  "service_terms",
  "privacy_account",
  "child_basic",
  "child_health",
] as const;
export type ConsentScope = (typeof CONSENT_SCOPES)[number];

export interface ConsentItem {
  scope: ConsentScope;
  /**
   * 어디로 보내는가.
   * - `account` → `POST /auth/{provider}/signup` 의 `consents` (계정이 그때 만들어진다)
   * - `child`   → `POST /consents` (가입 직후. 🚨 `child_basic` 없이 `POST /children` 은 403 이라
   *               아이를 만들기 **전에** 받아야 한다 · 계약서 §04)
   */
  target: "account" | "child";
  label: string;
  description: string;
  /** 화면에 그대로 보여준다 — 무엇에 동의하는지 근거를 숨기지 않는다. */
  legalBasis?: string;
  /**
   * 🚨 민감정보는 다른 동의와 **구분해서** 받아야 한다 (개인정보보호법 제23조).
   * 그래서 이 화면에는 "전체 동의" 가 없다 — 한 번에 쓸어 담으면 그 구분이 사라진다.
   */
  sensitive?: boolean;
}

/** 아이를 처음 등록하는 보호자(owner)가 가입 시점에 받는 4건. 전부 필수다. */
export const SIGNUP_CONSENTS: ConsentItem[] = [
  {
    scope: "service_terms",
    target: "account",
    label: "서비스 이용약관",
    description: "보호자 계정으로 이 서비스를 쓰는 데 동의해요. 보호자마다 한 번만 받아요.",
  },
  {
    scope: "privacy_account",
    target: "account",
    label: "개인정보 수집·이용 — 보호자 본인",
    description:
      "로그인에 쓰는 계정 식별값만 저장해요. 이메일·프로필 사진은 받아도 저장하지 않아요.",
  },
  {
    scope: "child_basic",
    target: "child",
    label: "개인정보 수집·이용 — 아이 기본정보",
    description: "별명 · 생일 · 관계까지만 받아요. 이 동의가 없으면 아이를 등록할 수 없어요.",
    legalBasis: "개인정보보호법 제22조의2 (만 14세 미만 아동의 법정대리인 동의)",
  },
  {
    scope: "child_health",
    target: "child",
    label: "민감정보 처리 — 아이 건강·알레르기",
    description:
      "알레르기와 건강 기록은 식사 제안을 걸러내고 증상을 정리하는 데만 써요. 이 동의가 없으면 식사·건강 기능이 동작하지 않아요.",
    legalBasis: "개인정보보호법 제23조 (민감정보의 처리 — 별도 동의)",
    sensitive: true,
  },
];
