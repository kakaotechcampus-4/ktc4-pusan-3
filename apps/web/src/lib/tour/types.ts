/**
 * 화면 투어의 모양 (#632 · `docs/web/screen-tour-v1.md`).
 *
 * 투어 하나 = 화면 하나의 단계 목록. 단계는 화면의 요소 하나를 `data-tour` 값으로 가리킨다.
 */

/**
 * 투어 이름. "봤음" 을 이 이름으로 센다.
 *
 * 🚨 **이름을 바꾸면 이미 본 사람에게 다시 뜬다.** 화면이 바뀌어도 다시 띄우지 않기로 했으므로
 *    (`docs/web/screen-tour-v1.md` §2 · 버전 숫자 없음) 이름은 처음 정한 그대로 둔다.
 * 🚨 홈은 둘이다 — 기록이 없을 때(`home-empty`)와 생긴 뒤(`home`). 가입 직후에는 빈 상태 단계만 보여 주고,
 *    나머지는 첫 기록이 생긴 뒤 홈에 다시 왔을 때 보여 준다 (`docs/web/screen-tour-v1.md` §2).
 */
export const TOUR_KEYS = [
  "home-empty",
  "home",
  "chat",
  "suggestions",
  "memories",
  "calendar",
  "profile",
  "growth",
  "settings",
] as const;

export type TourKey = (typeof TOUR_KEYS)[number];

/**
 * 기기 저장소의 "봤음" 키 (`lib/tour/seen.ts`). 🚨 e2e 공통 바탕(`e2e/fixtures.ts`)이 이 키로 "다 봤음" 을
 * 심어서 다른 화면 테스트에 투어가 끼어들지 않게 한다 — 그래서 여기(화면 코드를 안 부르는 파일)에 둔다.
 */
export function seenStorageKey(parentId: string): string {
  return `icatch.tour.seen.${parentId}`;
}

export interface TourStep {
  /** 비출 요소의 `data-tour` 값. 🚨 화면에 그 요소가 없으면 이 단계는 빠지고 분모도 줄어든다 (`docs/web/screen-tour-v1.md` §3). */
  target: string;
  title: string;
  /** 🚨 화면에 적힌 이름 그대로 부른다. Agent · Memory · run 같은 안쪽 말을 쓰지 않는다 (`docs/web/screen-tour-v1.md` §4). */
  body: string;
  /**
   * 이 투어를 이미 봤으면 이 단계를 뺀다 (`docs/web/screen-tour-v1.md` §2). 홈의 입력창처럼 `home-empty` 에서 이미 설명한 것을
   * `home` 에서 다시 말하지 않으려고 쓴다 — 초대받아 바로 기록이 있는 홈에 온 보호자는 둘 다 본다.
   */
  skipIfSeen?: TourKey;
  /**
   * 이 `data-tour` 의 **마지막** 요소 안에서만 찾는다. 없으면 화면 전체에서 첫 번째를 찾는다.
   * 04 대화는 `"turn"` — 단계마다 따로 "마지막" 을 고르면 말풍선은 마지막 턴, 저장 결과는 그 앞 턴을
   * 비추며 스크롤이 위아래로 튄다. 한 턴을 범위로 잡아 그 안에서만 짚는다 (#632 리뷰).
   */
  within?: string;
}

export interface TourDefinition {
  key: TourKey;
  /** 🚨 `false` 면 처음 들어와도 저절로 뜨지 않고 ? 로만 연다 (10 설정 · §1). */
  auto?: boolean;
  /** 🚨 6개 이하 (`docs/web/screen-tour-v1.md` §3). 자동으로 뜬 투어에만 마지막 "? 로 다시 보기" 단계가 붙는다. */
  steps: readonly TourStep[];
}

/** 자동으로 뜬 투어 끝에 붙는 단계. ? 버튼이 `data-tour="help"` 다. */
export const HELP_STEP: TourStep = {
  target: "help",
  title: "다시 보고 싶을 때",
  body: "이 설명은 여기 물음표를 누르면 언제든 다시 볼 수 있어요.",
};
