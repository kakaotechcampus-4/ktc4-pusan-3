import { create } from "zustand";

import type { MedicationDraft } from "@/lib/api/types";

/**
 * 아직 제출하지 않은 **복약 초안** (#537). 아이별로 묶어 둔다.
 *
 * 🚨 **`persist` 를 붙이지 않는다.** 일정 초안(`stores/event-draft.ts`)은 세션 스토리지에 두지만, 복약 초안에는
 *    약 이름 · 용량 · 복용 이유가 들어 있다 — **건강 정보**다 (최상위 §2 개인정보). 디스크에 남기지 않는다.
 *    대가로 새로고침하면 초안이 사라지고, 다시 받으려면 대화로 다시 말해야 한다. 초안은 서버에도 남지 않는다.
 * 🚨 **로그아웃에서 비운다** (`stores/session.ts` 의 `clearAll()`). 🚨 콘솔 · 로그에 찍지 않는다.
 *
 * 🚨 **같은 코스(`schedule_id`)의 수정 초안은 최신 한 장만 남긴다.** 낡은 수정 초안을 나중에 내면 뒤에 낸
 *    수정이 되돌아간다 — 일정 초안의 `mergeDrafts` 와 같은 이유다. `create` 는 합칠 기준이 없어 그대로 쌓는다.
 *
 * 🚨 **얹는 것은 도착할 때 한 번이다** (`seed`). 대화의 시트는 열 때마다 새로 그려져서, 거기서 얹으면 제출하고
 *    뺀 장이 다시 열 때 되살아난다 (`stores/conversation.ts` 의 `seedDrafts` 와 같은 이유).
 */
interface MedicationDraftState {
  /** childId → 아직 제출하지 않은 초안들. */
  byChild: Record<string, MedicationDraft[]>;
  /** 이미 한 번 얹은 `draft_id`. 다시 시도로 같은 run 을 처음부터 다시 받아도 두 번 얹지 않는다. */
  seen: Set<string>;
  seed: (childId: string, drafts: MedicationDraft[]) => void;
  /** 제출한 한 장을 뗀다. */
  remove: (childId: string, draftId: string) => void;
  /** 로그아웃에서 부른다. */
  clearAll: () => void;
}

/**
 * 🚨 **같은 `schedule_id` 는 새 것이 이긴다.** `draft_id` 가 같으면 같은 장이라 그것도 갈아 끼운다.
 * 그리는 목록도 이 함수를 쓴다 — 화면에 낡은 장이 남아 제출 버튼을 가지면 스토어가 막으려던 사고가 되살아난다.
 */
export function mergeMedicationDrafts(
  current: readonly MedicationDraft[],
  incoming: readonly MedicationDraft[],
): MedicationDraft[] {
  const draftIds = new Set(incoming.map((d) => d.draft_id));
  const scheduleIds = new Set(incoming.flatMap((d) => (d.schedule_id ? [d.schedule_id] : [])));
  const kept = current.filter(
    (d) => !draftIds.has(d.draft_id) && !(d.schedule_id && scheduleIds.has(d.schedule_id)),
  );
  return [...kept, ...incoming];
}

export const useMedicationDraftStore = create<MedicationDraftState>()((set) => ({
  byChild: {},
  seen: new Set(),

  seed: (childId, drafts) =>
    set((s) => {
      const fresh = drafts.filter((d) => !s.seen.has(d.draft_id));
      if (fresh.length === 0) return s;
      return {
        seen: new Set([...s.seen, ...fresh.map((d) => d.draft_id)]),
        byChild: {
          ...s.byChild,
          [childId]: mergeMedicationDrafts(s.byChild[childId] ?? [], fresh),
        },
      };
    }),

  remove: (childId, draftId) =>
    set((s) => {
      const current = s.byChild[childId];
      if (!current) return s;
      return {
        byChild: { ...s.byChild, [childId]: current.filter((d) => d.draft_id !== draftId) },
      };
    }),

  clearAll: () => set({ byChild: {}, seen: new Set() }),
}));

/** 🚨 매번 새 배열을 만들면 셀렉터가 렌더마다 다른 값을 돌려줘 무한 렌더가 된다. */
export const NO_MEDICATION_DRAFTS: MedicationDraft[] = [];
