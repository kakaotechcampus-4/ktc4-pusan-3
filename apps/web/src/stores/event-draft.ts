import { create } from "zustand";
import { persist, createJSONStorage } from "zustand/middleware";

import type { EventDraft } from "@/lib/api/types";

/**
 * 아직 제출하지 않은 **일정 초안.** 아이별로 묶어 둔다.
 *
 * 🚨 **초안은 서버에 없다** (9/21 회의 · #118). `event` 테이블에는 보호자가 제출한 행만 들어가고,
 *    Agent 는 SSE 로 초안을 내보내는 데까지만 한다. 그래서 **화면이 들고 있지 않으면 사라진다.**
 *
 * 🚨 **같은 `event_id` 는 최신 한 장만 남긴다** (#122 에서 FE 가 맡기로 한 1차 방어).
 *    초안 A·B 를 띄워 두고 **나중 것부터 제출하면 앞 초안이 뒤 결과를 지운다** — `items` 가
 *    최종 목록이라 A 에 없는 준비물이 삭제로 처리되기 때문이다 (시하님이 "모자 증발" 로 재현).
 *    `DraftBook` 이 한 run 안에서 하는 병합을 화면이 run 밖으로 연장하는 셈이라, A 가 애초에 안 남는다.
 *    잠금은 후속이고 덮어쓰기는 허용으로 정해졌다 — 이 스토어가 그 결정 위에서 할 수 있는 전부다.
 *
 * 🚨 **`create` 초안은 합치지 않는다.** `event_id` 가 없어 같은 일정인지 알 방법이 없다 —
 *    "금요일 물놀이 있어" 를 두 번 말하면 카드가 두 장 남고 둘 다 제출하면 일정이 두 건 생긴다.
 *    제목·시각이 같아도 다른 일정일 수 있어서 서버도 화면도 합칠 근거가 없다 (#122 재리뷰 (2)).
 *    **아는 채로 둔다** — 보호자가 카드를 보고 한 장만 내는 것에 기댄다.
 *
 * ## 🚨 왜 이 스토어만 `persist` 를 쓰나
 *
 * `stores/draft.ts`(아직 안 보낸 발화 원문) · `photo-draft.ts`(아이 사진) ·
 * `safety-scan-draft.ts`(검사지 사진 — 의료 기록)는 **`persist` 금지**다. 여기는 다르다:
 *
 * - 담는 것이 **발화 원문도 사진도 아니다.** 일정 제목·시각·준비물이고, 보호자가 카드에서
 *   보고 고칠 값이다. 아이에 대한 원문은 여전히 `draft.ts` 가 메모리에만 들고 있다.
 * - `sessionStorage` 라 **탭을 닫으면 사라진다** (`localStorage` 금지 — 세션 토큰과 같은 규칙).
 * - 없으면 새로고침 한 번에 Agent 가 만든 초안이 통째로 날아가고, 되받을 경로가 서버에 없다.
 *
 * 🚨 그래도 **로그아웃에서 비운다** (`stores/session.ts` 의 `clearAll()`). 다음 사람에게
 *    남의 아이 일정을 넘기지 않는다.
 * 🚨 **콘솔·로그에 찍지 않는다** — 다른 셋과 같은 규칙이다 (최상위 CLAUDE.md §2).
 */
interface EventDraftState {
  /** childId → 아직 제출하지 않은 초안들. 없는 아이는 빈 배열로 읽힌다. */
  byChild: Record<string, EventDraft[]>;

  /** run 이 내보낸 묶음을 얹는다. 같은 `event_id` 가 있으면 **새 것으로 갈아 끼운다.** */
  addDrafts: (childId: string, drafts: EventDraft[]) => void;
  /** 제출했거나 보호자가 닫은 한 장을 뗀다. */
  removeDraft: (childId: string, draftId: string) => void;
  clearChild: (childId: string) => void;
  /** 로그아웃에서 부른다. */
  clearAll: () => void;
}

/**
 * 🚨 **같은 `event_id` 는 새 것이 이긴다.** `event_id` 가 `null`(create)이면 합칠 기준이 없어
 *    그대로 쌓는다. `draft_id` 가 같으면 같은 장이므로 그것도 갈아 끼운다.
 */
export function mergeDrafts(current: EventDraft[], incoming: EventDraft[]): EventDraft[] {
  const replacedEventIds = new Set(
    incoming.map((d) => d.event_id).filter((id): id is string => id !== null),
  );
  const replacedDraftIds = new Set(incoming.map((d) => d.draft_id));

  const kept = current.filter(
    (d) =>
      !replacedDraftIds.has(d.draft_id) &&
      !(d.event_id !== null && replacedEventIds.has(d.event_id)),
  );
  return [...kept, ...incoming];
}

export const useEventDraftStore = create<EventDraftState>()(
  persist(
    (set) => ({
      byChild: {},

      addDrafts: (childId, drafts) =>
        set((s) => ({
          byChild: { ...s.byChild, [childId]: mergeDrafts(s.byChild[childId] ?? [], drafts) },
        })),

      removeDraft: (childId, draftId) =>
        set((s) => {
          const current = s.byChild[childId];
          if (!current) return s;
          return {
            byChild: { ...s.byChild, [childId]: current.filter((d) => d.draft_id !== draftId) },
          };
        }),

      clearChild: (childId) =>
        set((s) => {
          if (!(childId in s.byChild)) return s;
          const next = { ...s.byChild };
          delete next[childId];
          return { byChild: next };
        }),

      clearAll: () => set({ byChild: {} }),
    }),
    {
      name: "icatch.event-drafts",
      // 🚨 `localStorage` 가 아니다 — 탭을 닫으면 같이 사라져야 한다.
      storage: createJSONStorage(() => sessionStorage),
    },
  ),
);

/** 화면에서는 배열 하나로 읽는다. 어디에 사는지는 화면이 알 필요가 없다. */
export function useEventDrafts(childId: string): EventDraft[] {
  return useEventDraftStore((s) => s.byChild[childId] ?? EMPTY);
}

/** 🚨 매번 새 배열을 만들면 셀렉터가 렌더마다 다른 값을 돌려줘 무한 렌더가 된다. */
const EMPTY: EventDraft[] = [];
