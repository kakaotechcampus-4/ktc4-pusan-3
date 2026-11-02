import { beforeEach, describe, expect, it } from "vitest";

import type { MedicationDraft } from "@/lib/api/types";

import { mergeMedicationDrafts, useMedicationDraftStore } from "./medication-draft";

/** 복약 초안 보관함 (#537). 🚨 지어낸 약 이름이다 (최상위 §9). */

const draft = (over: Partial<MedicationDraft>): MedicationDraft => ({
  draft_id: "d1",
  op: "create",
  source: "utterance",
  schedule: {
    title: "기침 시럽",
    dosage: null,
    indication_text: null,
    storage: null,
    med_form: null,
    note: null,
    symptom_codes: [],
    starts_on: "2026-10-23",
    ends_on: "2026-10-25",
  },
  doses: [
    { timing_anchor: "breakfast", offset_minutes: 30, fixed_time: null, scheduled_time: "08:30" },
  ],
  notice_times: ["08:30"],
  missing: [],
  ...over,
});

beforeEach(() => {
  useMedicationDraftStore.getState().clearAll();
});

describe("mergeMedicationDrafts", () => {
  it("🚨 같은 코스의 수정 초안은 새 것이 이긴다 — 낡은 것을 나중에 내면 뒤 수정이 되돌아간다", () => {
    const old = draft({ draft_id: "u1", op: "update", schedule_id: "ms_1" });
    const fresh = draft({ draft_id: "u2", op: "update", schedule_id: "ms_1" });
    expect(mergeMedicationDrafts([old], [fresh]).map((d) => d.draft_id)).toEqual(["u2"]);
  });

  it("새 복약(create)은 합칠 기준이 없어 그대로 쌓는다", () => {
    const merged = mergeMedicationDrafts([draft({ draft_id: "c1" })], [draft({ draft_id: "c2" })]);
    expect(merged.map((d) => d.draft_id)).toEqual(["c1", "c2"]);
  });
});

describe("보관함", () => {
  it("한 번 얹은 초안은 다시 얹지 않는다 — 넣고 뺀 장이 다시 시도로 되살아나지 않게", () => {
    const store = useMedicationDraftStore.getState();
    store.seed("c1", [draft({})]);
    useMedicationDraftStore.getState().remove("c1", "d1");
    useMedicationDraftStore.getState().seed("c1", [draft({})]);
    expect(useMedicationDraftStore.getState().byChild.c1).toEqual([]);
  });

  it("🚨 `persist` 를 붙이지 않는다 — 약 이름 · 복용 이유가 든 건강 정보다", () => {
    // zustand 의 persist 미들웨어는 스토어에 `persist` API 를 단다. 없으면 디스크에 쓰지 않는다.
    expect("persist" in useMedicationDraftStore).toBe(false);
  });
});
