import { afterEach, describe, expect, it } from "vitest";

import type { EventDraft } from "@/lib/api/types";
import { mergeDrafts, useEventDraftStore, type StoredDraft } from "@/stores/event-draft";

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

describe("dropDrafts", () => {
  const CHILD = "c1";
  const store = () => useEventDraftStore.getState();
  const stored = () => store().byChild[CHILD] ?? [];

  afterEach(() => store().clearAll());

  it("🚨 그 경로의 남은 초안을 전부 뗀다 — 전에 넣지 않은 제안 초안이 다음 시트의 첫 장으로 서면 안 된다 (#539)", () => {
    store().addDrafts(CHILD, "suggestion", [
      draft({ draft_id: "a", suggestion_ids: ["s1"] }).draft,
      draft({ draft_id: "b", suggestion_ids: ["s2"] }).draft,
    ]);
    store().dropDrafts(CHILD, "suggestion");

    expect(stored()).toEqual([]);
  });

  it("다른 경로의 초안은 그대로 둔다 — 04 · 08 초안은 그 화면이 되읽는다", () => {
    store().addDrafts(CHILD, "input", [draft({ draft_id: "input" }).draft]);
    store().addDrafts(CHILD, "suggestion", [draft({ draft_id: "old" }).draft]);
    store().addDrafts(CHILD, "photo", [draft({ draft_id: "photo" }).draft]);
    store().dropDrafts(CHILD, "suggestion");

    expect(stored().map((d) => [d.origin, d.draft.draft_id])).toEqual([
      ["input", "input"],
      ["photo", "photo"],
    ]);
  });

  it("다른 아이의 초안은 건드리지 않는다", () => {
    store().addDrafts("c2", "suggestion", [draft({ draft_id: "other-child" }).draft]);
    store().dropDrafts(CHILD, "suggestion");

    expect(ids(store().byChild.c2 ?? [])).toEqual(["other-child"]);
  });
});
