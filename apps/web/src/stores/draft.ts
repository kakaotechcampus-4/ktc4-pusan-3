import { create } from "zustand";

/**
 * 03 홈 채팅바에 **아직 보내지 않은 한 줄.** 아이별로 하나.
 *
 * 🚨 **이건 라우트보다 오래 살아야 한다.** 원래 이 값은 홈 화면의 `useState` 였는데,
 *    하단 네비가 생기면서 보내기 버튼 21px 아래에 다른 화면으로 가는 문이 세 개 열렸다.
 *    세 줄 쓰다 잘못 누르면 언마운트와 함께 원문이 죽고, 되돌릴 방법이 없었다.
 *    같은 이유로 04 저장 결과를 별도 라우트로 안 만들었는데(apps/web/CLAUDE.md §3)
 *    네비 쪽 문을 놓친 것이다. 승인을 하나 더 다는 대신(§2 — 승인 게이트는 딱 2곳)
 *    값을 화면 밖으로 옮긴다.
 *
 * 🚨 **저장소에 쓰지 않는다 — `persist` 를 붙이지 말 것.** 아이에 대한 발화 원문이라
 *    디스크에 남으면 안 된다 (최상위 CLAUDE.md §2 개인정보 — 로그·저장소에 원문 대신
 *    `memory_id`). 메모리에만 두면 탭을 닫을 때 같이 사라지고, 그 사이의 화면 이동은
 *    전부 살아남는다. 지켜야 할 것과 지키고 싶은 것이 둘 다 된다.
 *
 * 🚨 **콘솔·로그에 찍지 않는다.** 같은 규칙이다.
 */
interface DraftState {
  /** childId → 아직 보내지 않은 원문. 없는 아이는 빈 문자열로 읽힌다. */
  byChild: Record<string, string>;

  setDraft: (childId: string, text: string) => void;
  clearDraft: (childId: string) => void;
  /** 로그아웃에서 부른다 — 다음 사람에게 남의 아이 이야기를 넘기지 않는다. */
  clearAll: () => void;
}

export const useDraftStore = create<DraftState>()((set) => ({
  byChild: {},

  setDraft: (childId, text) => set((s) => ({ byChild: { ...s.byChild, [childId]: text } })),
  clearDraft: (childId) =>
    set((s) => {
      if (!(childId in s.byChild)) return s;
      const next = { ...s.byChild };
      delete next[childId];
      return { byChild: next };
    }),
  clearAll: () => set({ byChild: {} }),
}));

/** 화면에서는 `useState` 처럼 쓴다. 값이 어디에 사는지는 화면이 알 필요가 없다. */
export function useDraftText(childId: string): [string, (text: string) => void] {
  const text = useDraftStore((s) => s.byChild[childId] ?? "");
  const setDraft = useDraftStore((s) => s.setDraft);
  return [text, (next) => setDraft(childId, next)];
}
