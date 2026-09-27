import { create } from "zustand";

/**
 * 04 결과에서 Memory 가 되물은 질문 **한 개.** 아이별로 하나.
 *
 * 부모가 "이어서 적기" 를 누르면 04 결과가 닫히고 03 홈으로 돌아가는데, 그때 **무엇에 답하는
 * 중인지**가 화면에서 사라진다. 질문은 run 상태(`useRunStream`)에 있고 run 은 그 순간 리셋되기
 * 때문이다. 그래서 질문만 run 밖으로 한 칸 옮겨 둔다 (#141).
 *
 * 🚨 **`stores/draft.ts` 와 같은 규칙이다 — `persist` 를 붙이지 말 것.** 되묻는 질문에는 아이
 *    이야기가 그대로 들어 있다("어제부터 그랬나요?"). 디스크에 남기지 않는다
 *    (최상위 CLAUDE.md §2 개인정보). 로그아웃에서 `clearAll()` 로 지운다 (`stores/session.ts`).
 * 🚨 **콘솔·로그에 찍지 않는다.** 같은 규칙이다.
 *
 * 🚨 **답을 강요하는 자리가 아니다.** 화면에는 닫는 길을 함께 둔다 — 되묻기에 답하지 않아도
 *    이미 저장된 것은 그대로 남아 있고(§4 "기본값은 기록만"), 답하려면 한 줄을 새로 보내야
 *    해서 하루 입력 횟수(#147)를 한 번 더 쓴다.
 */
interface PendingQuestionState {
  /** childId → Memory 가 되물은 질문. 없는 아이는 `null` 로 읽힌다. */
  byChild: Record<string, string>;

  setQuestion: (childId: string, text: string) => void;
  clearQuestion: (childId: string) => void;
  /** 로그아웃에서 부른다 — 다음 사람에게 남의 아이 이야기를 넘기지 않는다. */
  clearAll: () => void;
}

export const usePendingQuestionStore = create<PendingQuestionState>()((set) => ({
  byChild: {},

  setQuestion: (childId, text) => set((s) => ({ byChild: { ...s.byChild, [childId]: text } })),
  clearQuestion: (childId) =>
    set((s) => {
      if (!(childId in s.byChild)) return s;
      const next = { ...s.byChild };
      delete next[childId];
      return { byChild: next };
    }),
  clearAll: () => set({ byChild: {} }),
}));

/** 화면에서 읽는 자리. 질문이 없으면 `null` 이다. */
export function usePendingQuestion(childId: string): string | null {
  return usePendingQuestionStore((s) => s.byChild[childId] ?? null);
}
