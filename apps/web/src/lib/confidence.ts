import type { ConfidenceSource } from "@/lib/api/types";

/**
 * 발화의 출처. 🚨 **서버 enum 을 화면 문구로 옮기는 표다 — 추측을 섞지 않는다.**
 *
 * 🚨 **한 곳에 둔다.** 04 대화 · 07 기억 상세 · 05 제안의 근거 목록이 같은 값을 그린다.
 *    파일마다 따로 두면 한쪽만 고쳐져 같은 출처가 화면마다 다르게 불린다 —
 *    실제로 관계 라벨에서 그 사고가 났다 (`lib/relation.ts` 의 🚨).
 */
export const CONFIDENCE_LABEL: Record<ConfidenceSource, string> = {
  institution_notice: "기관 공지",
  parent_direct: "보호자 직접",
  parent_hedged: "보호자 추측",
  parent_hearsay: "전해 들음",
};
