import { describe, expect, it } from "vitest";

import type { EventDraft } from "@/lib/api/types";
import { mergeDrafts, type StoredDraft } from "@/stores/event-draft";

function draft(overrides: Partial<EventDraft> & Pick<EventDraft, "draft_id">): StoredDraft {
  return {
    origin: "suggestion",
    draft: {
      op: "create",
      event_id: null,
      event: {
        title: "지어낸 일정",
        starts_at: null,
        ends_at: null,
        all_day: true,
        event_type: "episodic",
        category: "activity",
      },
      before: null,
      items: [],
      ...overrides,
    },
  };
}

const ids = (drafts: StoredDraft[]) => drafts.map((d) => d.draft.draft_id);

describe("mergeDrafts", () => {
  it("🚨 같은 제안이 걸린 초안은 새 것이 이긴다 — 초안 id 는 요청마다 다르다 (#241)", () => {
    const before = [draft({ draft_id: "old", suggestion_ids: ["s1"] })];
    const merged = mergeDrafts(before, [draft({ draft_id: "new", suggestion_ids: ["s1"] })]);

    expect(ids(merged)).toEqual(["new"]);
  });

  it("🚨 묶인 식사 초안은 제안 하나만 겹쳐도 같은 장이다", () => {
    const before = [draft({ draft_id: "old", suggestion_ids: ["s1", "s2"] })];
    const merged = mergeDrafts(before, [draft({ draft_id: "new", suggestion_ids: ["s2", "s3"] })]);

    expect(ids(merged)).toEqual(["new"]);
  });

  it("다른 제안의 초안은 그대로 둔다", () => {
    const before = [draft({ draft_id: "other", suggestion_ids: ["s9"] })];
    const merged = mergeDrafts(before, [draft({ draft_id: "new", suggestion_ids: ["s1"] })]);

    expect(ids(merged)).toEqual(["other", "new"]);
  });

  it("🚨 제안이 없는 create 초안은 합치지 않는다 — 같은 일정인지 알 근거가 없다", () => {
    const before = [draft({ draft_id: "a" })];
    const merged = mergeDrafts(before, [draft({ draft_id: "b" })]);

    expect(ids(merged)).toEqual(["a", "b"]);
  });
});
