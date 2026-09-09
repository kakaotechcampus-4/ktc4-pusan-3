/**
 * 이 화면이 검사할 색 쌍. 디자인 시스템 §2 표의 "무엇 위에 얹히는가" 를 코드로 옮긴 것이다.
 *
 * 🚨 §10 이 경고한 지점이 여기다 — **soft 배경 위 텍스트는 `canvas` 가 아니라 그 soft 색과
 *    비교해야 한다.** 그래서 쌍을 손으로 적는다. 자동으로 canvas 와만 비교하면 문서가
 *    통과했다고 말하는 값이 실제로는 다른 배경 위에 있는 상황을 못 잡는다.
 */

export interface ColorPair {
  /** 앞색 토큰 이름 (`--color-` 를 뺀 것). */
  fg: string;
  /** 뒷색 토큰 이름. */
  bg: string;
  /**
   * 기준이 갈린다 — 본문 4.5:1 · 비텍스트 3:1.
   * `info` 는 **문서가 기준을 주장하지 않는 값**이다 (장식용 경계 등). 숫자만 보여주고
   * 통과/미달을 매기지 않는다 — 근거 없는 기준으로 실패를 만들면 그 표를 아무도 안 믿는다.
   */
  kind: "text" | "non-text" | "info";
  note?: string;
}

export interface TokenGroup {
  title: string;
  /** 배경으로만 쓰이는 토큰 (대비 검사 대상 아님). */
  swatches: string[];
  pairs: ColorPair[];
}

export const COLOR_GROUPS: TokenGroup[] = [
  {
    title: "뉴트럴 — 화면의 90%",
    swatches: ["canvas", "surface", "surface-muted"],
    pairs: [
      { fg: "ink", bg: "canvas", kind: "text", note: "본문" },
      { fg: "ink-muted", bg: "canvas", kind: "text", note: "보조·설명" },
      { fg: "ink-subtle", bg: "canvas", kind: "text", note: "메타·건수" },
      { fg: "ink-subtle", bg: "surface-muted", kind: "text", note: "비활성 위 글자" },
      { fg: "line-strong", bg: "canvas", kind: "non-text", note: "입력 테두리" },
      // 🚨 `line` 에는 대비 기준이 없다. §2-1 이 3:1 을 주장한 것은 `line-strong` 뿐이고,
      //    카드 경계는 "그림자 대신 쓰는 장식" 이라 낮은 대비가 의도다 (§6). 숫자만 남긴다.
      { fg: "line", bg: "surface", kind: "info", note: "카드 경계 · 기준 없음(장식)" },
    ],
  },
  {
    title: "브랜드 — 세이지 그린",
    swatches: ["brand-soft"],
    pairs: [
      { fg: "brand", bg: "canvas", kind: "text", note: "링크·활성 탭" },
      { fg: "white", bg: "brand", kind: "text", note: "주 버튼 글자" },
      { fg: "white", bg: "brand-hover", kind: "text", note: "press" },
      { fg: "brand-ink", bg: "brand-soft", kind: "text", note: "개인화 카드 글자" },
    ],
  },
  {
    title: "도메인 4종",
    swatches: [],
    pairs: (["food", "activity", "education", "health"] as const).flatMap((d) => [
      { fg: d, bg: "canvas", kind: "text" as const },
      { fg: `${d}-ink`, bg: `${d}-soft`, kind: "text" as const, note: "soft 배경 위" },
    ]),
  },
  {
    title: "상태 — 승인 게이트 · 위험",
    swatches: [],
    pairs: [
      { fg: "caution", bg: "canvas", kind: "text" },
      { fg: "caution-ink", bg: "caution-soft", kind: "text", note: "배너 글자" },
      { fg: "danger", bg: "canvas", kind: "text" },
      { fg: "danger-ink", bg: "danger-soft", kind: "text", note: "배너 글자" },
      { fg: "white", bg: "danger", kind: "text", note: "파괴적 버튼" },
      { fg: "white", bg: "danger-hover", kind: "text", note: "press" },
    ],
  },
  {
    title: "외부 브랜드 — 카카오 로그인 전용",
    swatches: [],
    pairs: [
      { fg: "kakao-ink", bg: "kakao", kind: "text", note: "반투명 검정 85%" },
      { fg: "kakao-ink", bg: "kakao-hover", kind: "text", note: "press" },
    ],
  },
];

/** §4 타이포 8단계. 화면에서 실제 계산값을 읽어 표와 대조한다. */
export const TYPE_STEPS = [
  { name: "display", cls: "text-display", spec: "24 / 1.3 · 700 · -0.02em", use: "화면 제목" },
  {
    name: "title",
    cls: "text-title",
    spec: "20 / 1.35 · 700 · -0.01em",
    use: "카드 제목·시트 헤더",
  },
  { name: "section", cls: "text-section", spec: "17 / 1.4 · 600 · -0.01em", use: "섹션 구분" },
  { name: "body", cls: "text-body", spec: "16 / 1.6 · 400", use: "본문 기본값·입력값" },
  { name: "body-sm", cls: "text-body-sm", spec: "14 / 1.55 · 400", use: "보조 설명" },
  { name: "button", cls: "text-button", spec: "15 / 1.2 · 600", use: "버튼 라벨" },
  { name: "label", cls: "text-label", spec: "13 / 1.4 · 500", use: "칩·탭·폼 라벨" },
  {
    name: "caption",
    cls: "text-caption",
    spec: "12 / 1.4 · 400",
    use: "메타. 이보다 작게 쓰지 않는다",
  },
] as const;

/** §7 이 사양을 정했지만 아직 코드에 없는 것. 이 목록이 비면 §7 이 다 구현된 것이다. */
export const NOT_BUILT = [
  {
    name: "바텀시트",
    where: "09 캘린더 승인 · 06 승인 게이트",
    why: "네이티브 <dialog> 위에 얹는다",
  },
  { name: "배너 (caution · danger)", where: "06 승인 · 알레르기 저촉", why: "" },
  {
    name: "제안 카드 (개인화 · 일반 · 실패)",
    where: "05 제안",
    why: "🚨 개인화와 일반을 한 컴포넌트로 만들지 않는다",
  },
  { name: "근거 칩 (chip-evidence · -stale)", where: "05 · 07", why: "" },
  { name: "도메인 칩 (chip-domain)", where: "05 제안", why: "지금은 소개 화면이 직접 그린다" },
  { name: "탭", where: "07 기록 고치기 2계층", why: "전환을 URL 에 남긴다" },
  { name: "진행 오버레이", where: "04 저장 결과 (SSE)", why: "20초 넘기면 부분 결과로" },
  { name: "빈 상태 · 스켈레톤", where: "03 홈", why: "" },
] as const;
