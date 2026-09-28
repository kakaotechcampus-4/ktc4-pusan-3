import type { Gender } from "@/lib/api/types";

/**
 * 성별 정본. 11 아이 프로필과 02 온보딩이 **같은 목록·같은 라벨**을 쓴다.
 *
 * 🚨 화면마다 다시 쓰지 않는다 — 같은 값이 한쪽에서 "남자아이", 다른 쪽에서 "남아" 로
 *    보이면 같은 아이가 두 아이로 읽힌다 (`lib/relation.ts` 와 같은 이유로 모았다).
 *
 * 🚨 **"밝히지 않을래요" 가 기본값이다** (최상위 `CLAUDE.md` §2 · `Gender` 타입 머리말).
 *    한동안 `male | female` 둘만 두고 필수로 뒀다가(#75) 되돌렸다 — 둘 중 하나를 고르게
 *    만들면 안 밝히는 선택지가 화면에서 사라진다.
 * 🚨 **문구가 "모름" 이 아니다.** 보호자는 아이 성별을 알고 있고, 이 값은 *우리에게 알려
 *    줄지*를 고르는 것이다.
 */
export const GENDER_LABEL: Record<Gender, string> = {
  male: "남자아이",
  female: "여자아이",
  undisclosed: "밝히지 않을래요",
};

/** 🚨 새 아이의 출발값. 화면이 `male` 을 먼저 세워 두지 않는다. */
export const DEFAULT_GENDER: Gender = "undisclosed";

/**
 * 🚨 **셋뿐이라 드롭다운이 아니다** (`ChoiceField` 머리말) — 상자 안에 감췄다가 탭 두 번으로
 *    다시 보여줄 이유가 없고, `ChoiceField` 가 받는 "둘~셋" 안이다.
 * 🚨 **"밝히지 않을래요" 를 맨 끝에 둔다.** 기본값이지만 첫 칸에 세우면 고를 것이 있는 자리에
 *    "안 고름" 이 먼저 서서, 두 선택지가 그 뒤에 딸린 것처럼 읽힌다.
 */
export const GENDER_OPTIONS = [
  { value: "male", label: GENDER_LABEL.male },
  { value: "female", label: GENDER_LABEL.female },
  { value: "undisclosed", label: GENDER_LABEL.undisclosed },
] as const satisfies ReadonlyArray<{ value: Gender; label: string }>;
