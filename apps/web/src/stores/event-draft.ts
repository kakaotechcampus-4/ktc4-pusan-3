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
 * 🚨 **어느 경로에서 왔는지 함께 둔다** (`origin`). 한 아이의 초안을 한 배열에 담으면 04 에서
 *    안 낸 초안이 08 화면에 떠서 "사진에서 읽어냈어요" 라는 문구를 달고 선다 — 카드 `hint` 와
 *    목록 머리글에서 두 번 낸 것과 **같은 종류의 거짓말**이라 저장할 때부터 갈라 둔다.
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
/** 초안이 어느 경로에서 왔는가. 🚨 화면 문구가 이 값으로 갈린다. */
export type DraftOrigin = "input" | "photo" | "suggestion";

export interface StoredDraft {
  origin: DraftOrigin;
  draft: EventDraft;
}

interface EventDraftState {
  /** childId → 아직 제출하지 않은 초안들. 없는 아이는 빈 배열로 읽힌다. */
  byChild: Record<string, StoredDraft[]>;

  /** run 이 내보낸 묶음을 얹는다. 같은 `event_id` 가 있으면 **새 것으로 갈아 끼운다.** */
  addDrafts: (childId: string, origin: DraftOrigin, drafts: EventDraft[]) => void;
  /** 제출했거나 보호자가 닫은 한 장을 뗀다. */
  removeDraft: (childId: string, draftId: string) => void;
  /** 로그아웃에서 부른다. */
  clearAll: () => void;
}

/**
 * 🚨 **같은 `event_id` 는 새 것이 이긴다.** `event_id` 가 `null`(create)이면 합칠 기준이 없어
 *    그대로 쌓는다. `draft_id` 가 같으면 같은 장이므로 그것도 갈아 끼운다.
 *
 * 🚨 **경로를 가리지 않고 `event_id` 로 판정한다.** 같은 일정을 한 줄 입력으로도 사진으로도
 *    고칠 수 있고, 낡은 쪽이 남아 있으면 그것이 뒤 결과를 지운다 — 갈라 두는 것은 **문구**이지
 *    덮어쓰기 판정이 아니다.
 *
 * 🚨 **그리는 목록도 이 함수를 쓴다.** `draft_id` 만으로 거르면 같은 `event_id` 의 낡은 장이
 *    화면에 남아 자기 제출 버튼을 갖고, 그걸 나중에 누르면 새 장의 준비물이 지워진다 —
 *    스토어가 막으려던 바로 그 사고를 화면이 되살린다.
 */
export function mergeDrafts(current: StoredDraft[], incoming: StoredDraft[]): StoredDraft[] {
  const replacedEventIds = new Set(
    incoming.map((d) => d.draft.event_id).filter((id): id is string => id !== null),
  );
  const replacedDraftIds = new Set(incoming.map((d) => d.draft.draft_id));

  const kept = current.filter(
    (d) =>
      !replacedDraftIds.has(d.draft.draft_id) &&
      !(d.draft.event_id !== null && replacedEventIds.has(d.draft.event_id)),
  );
  return [...kept, ...incoming];
}

export const useEventDraftStore = create<EventDraftState>()(
  persist(
    (set) => ({
      byChild: {},

      addDrafts: (childId, origin, drafts) =>
        set((s) => ({
          byChild: {
            ...s.byChild,
            [childId]: mergeDrafts(
              s.byChild[childId] ?? [],
              drafts.map((draft) => ({ origin, draft })),
            ),
          },
        })),

      removeDraft: (childId, draftId) =>
        set((s) => {
          const current = s.byChild[childId];
          if (!current) return s;
          return {
            byChild: {
              ...s.byChild,
              [childId]: current.filter((d) => d.draft.draft_id !== draftId),
            },
          };
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

/** 🚨 매번 새 배열을 만들면 셀렉터가 렌더마다 다른 값을 돌려줘 무한 렌더가 된다. */
export const NO_DRAFTS: StoredDraft[] = [];
