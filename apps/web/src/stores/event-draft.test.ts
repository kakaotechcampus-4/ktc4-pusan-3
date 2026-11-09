import { afterEach, describe, expect, it } from "vitest";

import type { EventDraft, SeriesDraft } from "@/lib/api/types";
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

/** 반복 묶음 고치기 초안 (#738). */
function series(draftId: string, seriesId: string): StoredDraft {
  const repeat = { weekdays: [1, 3], until: "2026-12-20", label: "12월 20일까지 매주 월, 수" };
  const fields = {
    title: "지어낸 반복 일정",
    starts_time: "09:00",
    ends_time: null,
    all_day: false,
    event_type: "core",
    category: "institution",
  } satisfies SeriesDraft["event"];
  return {
    origin: "input",
    draft: {
      draft_id: draftId,
      op: "update_series",
      series_id: seriesId,
      event: fields,
      repeat,
      before: { ...fields, starts_time: "08:30", repeat, items: [] },
      items: [],
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

  it("🚨 같은 반복 묶음의 고치기 초안은 새 것이 이긴다 (#738) — 낡은 장을 나중에 내면 새 장을 되돌린다", () => {
    const merged = mergeDrafts([series("old", "series_1")], [series("new", "series_1")]);

    expect(ids(merged)).toEqual(["new"]);
  });

  it("다른 묶음의 초안과, id 가 같은 일정의 수정 초안은 그대로 둔다 — 묶음과 일정은 다른 대상이다", () => {
    const before = [
      series("other", "series_9"),
      draft({ draft_id: "event", op: "update", event_id: "series_1" }),
    ];
    const merged = mergeDrafts(before, [series("new", "series_1")]);

    expect(ids(merged)).toEqual(["other", "event", "new"]);
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
