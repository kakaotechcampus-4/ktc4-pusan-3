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
  /** "상세 보기" 시트에 펼쳐 보여줄 내용. */
  details: Array<{ heading: string; lines: string[] }>;
}

/**
 * 🚨 **이건 약관 전문이 아니다.** 지금 확정된 사실만 적었다.
 *
 * 보관 기간·삭제 범위가 아직 팀 미정이라(최상위 CLAUDE.md §10) 최종 약관을 쓸 수 없다.
 * 없는 조항을 그럴듯하게 지어 넣으면 그대로 배포되고, 아동 건강정보를 다루는 서비스에서
 * 그건 사고다. **확정되면 여기에 정식 문구를 넣고 이 안내를 지운다.**
 */
/**
 * 🚨 **"언제든 철회할 수 있어요" 를 쓰지 않는다.**
 *
 * 네 건이 전부 필수라, 하나라도 철회하면 서비스가 성립하지 않는다 —
 * `child_basic` 없이는 아이를 등록할 수 없고(`POST /children` → 403),
 * `child_health` 없이는 입력조차 저장되지 않는다(`POST /inputs` → 403).
 * 즉 철회는 "설정에서 스위치 하나 끄기" 가 아니라 **탈퇴에 가깝다.**
 * 가벼운 문구로 말하면 실제로 눌렀을 때와 다르다.
 *
 * 철회가 정확히 무엇을 지우는지는 아직 팀 미정이라(최상위 CLAUDE.md §10 삭제 범위)
 * 지금은 **아무 약속도 하지 않는다.** 확정되면 그때 정확한 문구를 넣는다.
 */
export const TERMS_NOT_FINAL =
  "확정된 내용만 적어 뒀어요. 보관 기간과 삭제 범위가 정해지면 정식 약관으로 바뀝니다.";

/** 아이를 처음 등록하는 보호자(owner)가 가입 시점에 받는 4건. 전부 필수다. */
export const SIGNUP_CONSENTS: ConsentItem[] = [
  {
    scope: "service_terms",
    target: "account",
    label: "서비스 이용약관",
    description: "보호자 계정으로 이 서비스를 쓰는 데 동의해요. 보호자마다 한 번만 받아요.",
    details: [
      {
        heading: "무엇에 동의하나요",
        lines: [
          "보호자 계정으로 이 서비스를 이용하는 데 동의합니다.",
          "계정 단위 동의라 보호자마다 한 번만 받습니다.",
        ],
      },
      {
        heading: "이 서비스가 하지 않는 것",
        lines: [
          "진단하거나 약을 권하지 않습니다.",
          "상품을 추천하거나 광고를 넣지 않습니다.",
          "보호자를 대신해 예약·발송·결제를 하지 않습니다.",
          "아이와 직접 대화하지 않습니다.",
        ],
      },
    ],
  },
  {
    scope: "privacy_account",
    target: "account",
    label: "개인정보 수집·이용 — 보호자 본인",
    description:
      "로그인에 쓰는 계정 식별값만 저장해요. 이메일·프로필 사진은 받아도 저장하지 않아요.",
    details: [
      { heading: "받는 것", lines: ["카카오 회원번호 (로그인 식별용)"] },
      {
        heading: "받아도 저장하지 않는 것",
        lines: ["이메일 주소", "프로필 사진", "카카오 접근 토큰 (교환 즉시 폐기합니다)"],
      },
      { heading: "쓰는 곳", lines: ["로그인과 세션 유지. 그 밖의 용도로 쓰지 않습니다."] },
      { heading: "제3자 제공", lines: ["하지 않습니다."] },
    ],
  },
  {
    scope: "child_basic",
    target: "child",
    label: "개인정보 수집·이용 — 아이 기본정보",
    description: "별명 · 생일 · 관계까지만 받아요. 이 동의가 없으면 아이를 등록할 수 없어요.",
    legalBasis: "개인정보보호법 제22조의2 (만 14세 미만 아동의 법정대리인 동의)",
    details: [
      { heading: "받는 것", lines: ["별명 (실명이 아니어도 됩니다)", "생일", "아이와의 관계"] },
      {
        heading: "쓰는 곳",
        lines: [
          "나이에 맞는 제안을 고르고 일정을 계산하는 데 씁니다.",
          "나이는 저장하지 않고 생일로 그때그때 계산합니다.",
        ],
      },
      { heading: "받지 않는 것", lines: ["실명 · 주민등록번호 · 주소 · 연락처"] },
      {
        heading: "이 동의가 없으면",
        lines: ["아이를 등록할 수 없습니다. 서비스의 모든 기능이 아이 등록 위에 있습니다."],
      },
    ],
  },
  {
    scope: "child_health",
    target: "child",
    label: "민감정보 처리 — 아이 건강·알레르기",
    description:
      "알레르기와 건강 기록은 식사 제안을 걸러내고 증상을 정리하는 데만 써요. 이 동의가 없으면 식사·건강 기능이 동작하지 않아요.",
    legalBasis: "개인정보보호법 제23조 (민감정보의 처리 — 별도 동의)",
    sensitive: true,
    details: [
      {
        heading: "받는 것",
        lines: [
          "보호자가 직접 입력한 알레르기 · 식품 제한",
          "보호자가 남긴 증상 기록",
          "보호자가 올린 의료 기록",
        ],
      },
      {
        heading: "AI 가 만들지 않습니다",
        lines: [
          "알레르기와 건강 정보는 AI 가 생성하거나 추론하거나 고치지 않습니다.",
          "보호자가 입력한 값과 의료 기록만 저장합니다.",
        ],
      },
      {
        heading: "쓰는 곳",
        lines: [
          "식사 제안에서 못 먹는 것을 걸러냅니다. 이 걸러내기는 AI 가 아니라 코드가 합니다.",
          "증상 기록을 정리해서 보여줍니다.",
        ],
      },
      {
        heading: "하지 않는 것",
        lines: [
          "진단하지 않습니다. 같은 증상이 세 번 반복되면 추천을 멈추고 병원 확인을 권합니다.",
          "약을 권하지 않습니다.",
          "발달을 평가하지 않습니다.",
        ],
      },
      {
        heading: "동의하지 않으면",
        lines: [
          "식사와 건강 기능이 동작하지 않습니다. 알레르기를 모르는 채로 식사를 제안하지 않습니다.",
        ],
      },
    ],
  },
];
