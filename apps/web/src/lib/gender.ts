import type { Gender } from "@/lib/api/types";

/**
 * 성별 정본. 11 아이 프로필과 02 온보딩이 **같은 목록·같은 라벨**을 쓴다.
 *
 * 🚨 화면마다 다시 쓰지 않는다 — 같은 값이 한쪽에서 "남자아이", 다른 쪽에서 "남아" 로
 *    보이면 같은 아이가 두 아이로 읽힌다 (`lib/relation.ts` 와 같은 이유로 모았다).
 *
 * ⚠️ **"밝히지 않을래요" 가 없다** (#75 · `Gender` 타입 머리말). 최상위 `CLAUDE.md` §2 는
 *    그것을 기본값으로 적고 있어서 **문서와 코드가 아직 어긋나 있다** — 어느 쪽이 맞는지는
 *    팀 결정이고, 여기서 임의로 한쪽을 따라가지 않는다.
 *    🚨 대신 **02 에서는 안 고르고 넘어갈 수 있다.** 그 화면은 전부 선택이라, 안 고른 것을
 *       보내지 않는 것으로 "밝히지 않음" 을 표현한다 (없는 enum 값을 지어내지 않는다).
 */
export const GENDER_LABEL: Record<Gender, string> = {
  male: "남자아이",
  female: "여자아이",
};

/**
 * 🚨 둘뿐인 선택지라 드롭다운이 아니다 (`ChoiceField` 머리말) — 상자 안에 감췄다가
 *    탭 두 번으로 다시 보여줄 이유가 없다.
 */
export const GENDER_OPTIONS = [
  { value: "male", label: GENDER_LABEL.male },
  { value: "female", label: GENDER_LABEL.female },
] as const satisfies ReadonlyArray<{ value: Gender; label: string }>;
