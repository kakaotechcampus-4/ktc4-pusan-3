/**
 * 동의 화면이 쓰는 것 중 **서버가 주지 않는 것**만 여기 둔다.
 *
 * 🚨 **정본은 `GET /policies` 다** (#90 · #91 · #172). 무엇을 묻는지(scope 목록) · 어떤 순서로
 *    묻는지 · 제목 · 법적 근거 · 필수 여부 · 민감정보 여부 · 그리고 **동의로 기록될 버전**은
 *    전부 그 응답이 정한다. 예전에는 이 파일이 다 들고 있었는데, 그러면 보호자가 읽은 글과
 *    서버에 기록되는 글이 같은지 확인할 방법이 없다 (멘토 #71-4).
 *
 * 🚨 **약관 전문을 이 파일에 두지 않는다.** 전문은 서버가 저장해 둔 정본 HTML 이고
 *    (`policy.html_path`), 화면은 그 페이지를 **손대지 않고** 새 창으로 연다. 화면이 본문을
 *    직접 그리면 문단을 숨기거나 순서를 바꿀 수 있어 "보호자가 본 글" 을 증명하지 못한다.
 *
 * 그래서 여기 남는 것은 **약관이 아닌 안내 문구**뿐이다 — 체크박스 밑 한 줄, 설정 목록의
 * 짧은 이름, 켜고 끌 때 무엇이 달라지는지. 이건 서비스 화면의 말이라 약관 버전과 함께
 * 움직이지 않는다.
 */

import { API_BASE_URL } from "@/lib/env";
import type { Policy } from "@/lib/api/types";

export const CONSENT_SCOPES = [
  "service_terms",
  "privacy_account",
  "child_basic",
  "child_health",
  "location",
] as const;
export type ConsentScope = (typeof CONSENT_SCOPES)[number];

/**
 * **아이 단위로 기록되는 동의.** 01 아이 만들기 화면이 아이와 한 트랜잭션으로 보낸다 (#96) —
 * 동의를 아이 단위로 기록하면 `child_id` 없이는 저장할 수 없고, 그 id 는 아이를 만들어야 생긴다.
 *
 * 🚨 **여기 없는 scope 는 전부 계정 동의로 본다** (`consentChoices`). 서버가 아는 scope 를 프론트가
 *    모를 수 있어서, 모르는 것을 어느 쪽에 넣느냐가 실제로 갈린다 —
 *    ㉠ 계정 쪽에 넣으면 새 계정 동의가 가입 화면에 그냥 나온다 (아이 동의였다면 서버가
 *      `POST /auth/{provider}/signup` 에서 계정 scope 가 아닌 것을 무시하므로 손해가 없다),
 *    ㉡ 아이 쪽에 넣으면 새 **필수** 계정 동의를 아무도 못 켜서 **가입 자체가 막힌다.**
 *    되돌릴 수 있는 쪽으로 둔다.
 */
export const CHILD_CONSENT_SCOPES: readonly string[] = ["child_basic", "child_health"];

/** 이 동의가 어느 화면에서 만들어지는 것에 붙는가. */
export type ConsentTarget = "account" | "child";

export function consentTarget(scope: string): ConsentTarget {
  return CHILD_CONSENT_SCOPES.includes(scope) ? "child" : "account";
}

/* ── 화면 문구 ────────────────────────────────────────────────────────── */

/**
 * 🚨 **약관이 아니다.** 법적 표기(`label`) · 근거 조문(`legal_basis`) · 전문은 서버가 준다.
 *    여기 있는 것은 그 옆에 붙는 **우리 화면의 말**이다.
 */
export interface ConsentCopy {
  /**
   * 목록 줄에서 쓰는 짧은 이름. 🚨 **법적 표기를 대신하는 값이 아니다** — 줄에는 오른쪽에
   * 버튼이 서 있어서 긴 라벨이 두 줄로 접히는데, 접힌 라벨은 읽히지 않고 자리만 먹는다.
   * 무엇에 동의하는지의 정본은 그 줄에서 여는 **전문**(서버 정본 HTML)이다.
   */
  shortLabel: string;
  /** 체크박스 밑 한 줄. 전문을 대신하지 않는다 — 요약이다. */
  description: string;
  /**
   * 이 동의가 없을 때 **무엇이 멈추는가.** 10 설정의 철회 확인 시트가 이 문장들을 그대로
   * 보여준다 — 끄기 전에 결과를 먼저 읽는 것이 그 시트의 일이다.
   *
   * 🚨 필수 동의의 것은 "멈춘다" 가 아니라 "계정이 없어진다" 에 가깝다. 같은 필드에 담되
   *    화면이 다르게 말한다 (필수는 철회 버튼 자체가 없다).
   */
  blocks: string[];
  /**
   * 이 동의를 **받으면** 무엇이 되는가. 선택 동의에만 있다.
   *
   * 🚨 `blocks` 를 켜기 쪽에 돌려 쓰지 않는다. 그건 "없으면 멈추는 것" 이라, 켜는 시트에 그대로
   *    올리면 "동의하면 이 기능이 동작해요" 아래에 "이 기능이 멈춰요" 가 붙는다 (실제로 그랬다).
   */
  enables?: string[];
}

/**
 * 🚨 **필수/선택이 여기 없다.** 그건 서버가 지키는 규칙이고(`app/domains/policy/catalog.py`),
 *    같은 값을 두 곳에 적으면 어긋난다. 화면은 `policy.required` 만 본다.
 */
export const CONSENT_COPY: Record<ConsentScope, ConsentCopy> = {
  service_terms: {
    shortLabel: "서비스 이용약관",
    description: "보호자 계정으로 이 서비스를 쓰는 데 동의해요. 보호자마다 한 번만 받아요.",
    blocks: ["계정 자체가 이 동의 위에 있어서, 철회하면 서비스를 쓸 수 없어요."],
  },
  privacy_account: {
    shortLabel: "보호자 개인정보",
    description:
      "로그인에 쓰는 계정 식별값만 저장해요. 이메일·프로필 사진은 받아도 저장하지 않아요.",
    blocks: ["로그인을 할 수 없게 돼요. 계정을 알아볼 방법이 이것뿐이에요."],
  },
  child_basic: {
    shortLabel: "아이 기본정보",
    description: "별명, 생일, 관계까지만 받아요. 이 동의가 없으면 아이를 등록할 수 없어요.",
    blocks: ["아이를 등록할 수 없어요. 이 서비스의 모든 기능이 아이 등록 위에 있어요."],
  },
  child_health: {
    shortLabel: "아이 건강·알레르기",
    description:
      "알레르기와 건강 기록은 식사 제안을 걸러내고 증상을 정리하는 데만 써요. 이 동의가 없으면 식사·건강 기능이 동작하지 않아요.",
    /**
     * 🚨 **`blocks[0]` 이 10 설정의 줄에 그대로 나간다.** 필수 줄이 말해야 하는 것은
     *    "왜 못 끄는가" 라서 서비스 수준의 결과가 먼저 온다 (선택 동의는 반대로
     *    기능 단위의 결과가 먼저다).
     */
    blocks: [
      "한 줄 기록부터 저장되지 않아서, 철회하면 서비스를 쓸 수 없어요.",
      "식사 제안이 멈춰요. 알레르기를 모르는 채로 먹을 것을 권하지 않아요.",
      "건강 기록 정리와 증상 반복 알림이 멈춰요.",
    ],
  },
  location: {
    shortLabel: "위치정보",
    description:
      "지금 있는 지역의 날씨와 계절에 맞춰 놀이를 제안해요. 위치를 기록으로 저장하지는 않아요.",
    blocks: [
      "놀이 제안이 날씨와 계절을 못 봐요. 밖에서 놀 수 있는 날인지 판단하지 않고 제안해요.",
      "놀이 제안 자체는 그대로 나와요.",
    ],
    enables: [
      "지금 있는 지역의 날씨와 계절을 보고 놀이를 골라요.",
      "시·군·구 수준까지만 봐요. 정확한 좌표도, 이동 경로도 받지 않아요.",
      "위치를 아이의 기억으로 저장하지 않아요. 제안을 고를 때 한 번 쓰고 버려요.",
    ],
  },
};

export function consentCopy(scope: string): ConsentCopy | undefined {
  return (CONSENT_COPY as Record<string, ConsentCopy>)[scope];
}

/* ── 서버 응답 + 화면 문구 ────────────────────────────────────────────── */

/** 화면이 그리는 동의 한 줄 — 서버가 준 약관 + 그 옆에 붙는 우리 말. */
export interface ConsentChoice {
  policy: Policy;
  /**
   * 🚨 **없을 수 있다.** 서버가 새 scope 를 추가하면 문구가 아직 없다. 그때도 줄은 그린다 —
   *    제목 · 근거 · 전문은 서버가 주므로 **동의를 받을 수 있는 최소한은 갖춰져 있고**,
   *    모른다고 빼면 필수 동의가 화면에서 사라져 가입이 막힌다.
   */
  copy?: ConsentCopy;
}

/**
 * 그 화면이 물어볼 것만 골라 낸다. **정렬하지 않는다** — 배열 순서가 곧 서버가 정한 화면
 * 순서다 (계정 → 아이, 민감정보는 마지막).
 */
export function consentChoices(
  policies: readonly Policy[] | undefined,
  /** 생략하면 응답 전체. 10 설정은 계정·아이를 한 목록에 두고 `required` 로만 가른다. */
  target?: ConsentTarget,
): ConsentChoice[] {
  return (policies ?? [])
    .filter((policy) => target === undefined || consentTarget(policy.scope) === target)
    .map((policy) => ({ policy, copy: consentCopy(policy.scope) }));
}

/**
 * 전문 보기가 여는 주소. `html_path` 는 `/api/v1` 아래 경로라 기준 주소 뒤에 그대로 붙인다.
 *
 * 🚨 **`null` 이면 열 곳이 없다** — 화면은 버튼을 **숨긴다**. 다른 글로 대신하지 않는다.
 */
export function policyHref(policy: Policy): string | null {
  return policy.html_path === null ? null : `${API_BASE_URL}${policy.html_path}`;
}

/**
 * 지금까지 고른 동의 — **scope 마다 "어느 버전에 동의했는가"** 를 들고 있다.
 *
 * 🚨 boolean 이 아닌 이유가 핵심이다. 약관이 바뀌면 화면을 다시 불러오는데, 그때 체크가
 *    boolean 이면 **옛 글을 읽고 누른 체크가 새 글의 동의로 넘어간다.** 버전을 담아 두면
 *    바뀐 항목만 저절로 풀린다 (`isConsentChecked` 가 지금 버전과 비교한다).
 */
export type ConsentChecked = Partial<Record<string, string>>;

export function isConsentChecked(policy: Policy, checked: ConsentChecked): boolean {
  return checked[policy.scope] === policy.version;
}

/** 체크 한 번. 켜면 **그때 화면에 있던 버전**을 적어 두고, 끄면 지운다. */
export function toggleConsent(
  checked: ConsentChecked,
  policy: Policy,
  next: boolean,
): ConsentChecked {
  const updated = { ...checked };
  if (next) updated[policy.scope] = policy.version;
  else delete updated[policy.scope];
  return updated;
}

/**
 * 필수 항목이 전부 체크됐는가.
 *
 * 🚨 **막는 것은 `required` 뿐이다.** 목록 길이로 세지 않는다 — 선택 동의(`location`)가
 *    가입 화면에 서 있어서, 길이로 세면 그것까지 필수가 된다.
 */
export function requiredConsentsChecked(
  choices: readonly ConsentChoice[],
  checked: ConsentChecked,
): boolean {
  return choices
    .filter(({ policy }) => policy.required)
    .every(({ policy }) => isConsentChecked(policy, checked));
}

/**
 * 요청 바디에 실을 동의 목록.
 *
 * 🚨 **고른 것만 보낸다.** 안 고른 scope 까지 실어 보내면 화면이 물어본 것과 서버에 남는
 *    것이 달라진다.
 * 🚨 버전은 **지금 화면이 그린 그 버전**이다 (`policy.version`). 체크는 이미 같은 값으로
 *    비교해 걸러졌다.
 */
export function consentPayload(
  choices: readonly ConsentChoice[],
  checked: ConsentChecked,
): Array<{ scope: string; policy_version: string }> {
  return choices
    .filter(({ policy }) => isConsentChecked(policy, checked))
    .map(({ policy }) => ({ scope: policy.scope, policy_version: policy.version }));
}

/**
 * 400 `policy_version_invalid` — 화면이 낡았다는 뜻이다 (대기표는 살아 있다 ·
 * `docs/api/auth-kakao-v1.md` §3-5). 다시 불러오고 **바뀐 항목만** 다시 받는다.
 */
export const POLICY_CHANGED_MESSAGE = "약관이 바뀌었어요. 바뀐 항목을 다시 확인하고 동의해 주세요.";

/** 전문이 아직 없는 동의에 붙이는 한 줄. 🚨 없는 글을 지어 보여주는 대신 없다고 말한다. */
export const POLICY_TEXT_PENDING = "전문을 다듬고 있어요. 준비되면 여기에서 볼 수 있어요.";

/* ── 법적 문서 ────────────────────────────────────────────────────────── */

/**
 * 10 설정의 **약관과 방침**이 보여주는 것.
 *
 * 🚨 **동의 스코프와 다른 개념이다.** 동의 목록은 *무엇에 동의를 받는가* 이고, 여기 둘은
 *    *보호자가 언제든 다시 읽는 문서* 다. 설정에 동의 스코프를 늘어놓으면 가입 화면을 한 번
 *    더 그리는 셈이고, 부모가 거기서 하려는 일(약관 읽기)과도 어긋난다.
 *
 * 🚨 **이용약관은 사본을 두지 않는다** — `service_terms` 의 정본 HTML 을 그대로 연다.
 *    개인정보 처리방침만 아직 서버에 없어서(`docs/safety/consent-texts-v1.md` 머리말)
 *    화면이 들고 있고, 그래서 아래 `details` 가 남아 있다. **서버에 올라오면 지운다.**
 */
export interface LegalDocument {
  id: "service_terms" | "privacy_policy";
  label: string;
  legalBasis?: string;
  /** 서버 정본이 있는 문서. 이 scope 의 `html_path` 를 새 창으로 연다. */
  scope?: ConsentScope;
  /** 서버 정본이 아직 없는 문서만. 화면 안 시트로 보여준다. */
  details?: Array<{ heading: string; lines: string[] }>;
}

/**
 * 🚨 **이건 정식 개인정보 처리방침이 아니다.** 지금 확정된 사실만 모았다.
 *
 * 정식 방침에는 보관 기간 · 파기 절차 · 개인정보 보호책임자 · 안전성 확보조치 · 권리 행사
 * 방법이 들어가야 하는데, 그중 **보관 기간·삭제 범위·외부 모델 전달 범위**가 아직 팀 미정이다
 * (최상위 CLAUDE.md §10). 없는 조항을 그럴듯하게 지어 넣으면 그대로 배포되고, 아동 건강정보를
 * 다루는 서비스에서 그건 사고다. **확정되면 서버에 정본으로 올리고 이 상수를 지운다.**
 */
const PRIVACY_POLICY_DETAILS: NonNullable<LegalDocument["details"]> = [
  {
    heading: "받는 것",
    lines: [
      "카카오 회원번호 (로그인 식별용)",
      "아이의 별명 · 생일 · 아이와의 관계",
      "보호자가 직접 입력한 알레르기 · 건강 기록, 그리고 적어 주신 관찰",
      "위치정보에 동의하신 경우, 시·군·구 수준의 대략적인 위치",
    ],
  },
  {
    heading: "받아도 저장하지 않는 것",
    lines: [
      "이메일 주소 · 프로필 사진",
      "카카오 접근 토큰 (교환 즉시 폐기합니다)",
      "아이의 실명 · 주민등록번호 · 주소 · 연락처",
      "이동 경로와 방문한 장소",
    ],
  },
  {
    heading: "쓰는 곳",
    lines: [
      "로그인과 세션 유지",
      "나이에 맞는 제안을 고르고 일정을 계산하는 데. 나이는 저장하지 않고 생일로 그때그때 계산합니다.",
      "식사 제안에서 못 먹는 것을 걸러내는 데. 이 걸러내기는 AI 가 아니라 코드가 합니다.",
      "증상 기록을 정리해서 보여주는 데. 위치는 날씨와 계절을 보는 데 한 번 쓰고 버립니다.",
    ],
  },
  {
    heading: "하지 않는 것",
    lines: [
      "제3자에게 제공하지 않습니다.",
      "알레르기와 건강 정보를 AI 가 만들거나 추론하거나 고치지 않습니다.",
      "진단하지 않고, 발달을 평가하지 않으며, 상품을 추천하거나 광고를 넣지 않습니다.",
    ],
  },
  {
    heading: "아직 정리하고 있는 것",
    lines: [
      "정보를 얼마나 보관하는지, 지울 때 어디까지 지우는지",
      "제안을 만들 때 외부 모델로 어떤 항목까지 보내는지",
      "정해지면 이 방침을 먼저 고치고 알려드립니다.",
    ],
  },
];

/**
 * 🚨 개인정보 처리방침 시트에만 붙는다. 이용약관은 서버 정본이라 이 안내가 필요 없다 —
 *    거기 적힌 것은 지어낸 문장이 아니라 **보호자가 동의한 그 글**이다.
 */
export const TERMS_NOT_FINAL =
  "확정된 내용만 적어 뒀어요. 보관 기간과 삭제 범위가 정해지면 정식 문서로 바뀝니다.";

export const LEGAL_DOCUMENTS: LegalDocument[] = [
  {
    id: "service_terms",
    label: "서비스 이용약관",
    // 🚨 사본을 두지 않는다 — 가입 화면이 여는 것과 **같은 페이지**를 연다.
    scope: "service_terms",
  },
  {
    id: "privacy_policy",
    label: "개인정보 처리방침",
    legalBasis: "개인정보보호법 제30조 (개인정보 처리방침의 수립 및 공개)",
    details: PRIVACY_POLICY_DETAILS,
  },
];
