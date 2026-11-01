import { describe, expect, it } from "vitest";

import { ApiError, NetworkError } from "@/lib/api/errors";
import type { MedicationDraft } from "@/lib/api/types";

import { editableOf, failureOf, medicationDiff, missingFields, submitBodyOf } from "./medication";

/**
 * 복약 초안 카드의 판단 (#537). 🚨 지어낸 약 이름이다 (최상위 §9).
 */

const PATTERN: MedicationDraft["doses"] = [
  { timing_anchor: "breakfast", offset_minutes: 30, fixed_time: null, scheduled_time: "08:30" },
  { timing_anchor: "dinner", offset_minutes: 30, fixed_time: null, scheduled_time: "19:30" },
];

const CREATE: MedicationDraft = {
  draft_id: "d1",
  op: "create",
  source: null,
  schedule: {
    title: "기침 시럽",
    dosage: "3ml",
    indication_text: "기침",
    storage: null,
    med_form: null,
    note: null,
    symptom_codes: ["cough"],
    starts_on: "2026-10-23",
    ends_on: null,
  },
  doses: PATTERN,
  notice_times: ["08:30", "19:30"],
  missing: ["ends_on"],
};

describe("필수 칸", () => {
  it("비어 온 기간은 오늘로 채우지 않고 막는다 — 채우면 풀린다", () => {
    const fields = editableOf(CREATE.schedule);
    expect(fields.ends_on).toBe("");
    expect(missingFields(fields)).toEqual(["ends_on"]);
    expect(submitBodyOf(CREATE, fields)).toBeNull();

    expect(missingFields({ ...fields, ends_on: "2026-10-25" })).toEqual([]);
  });

  it("공백뿐인 약 이름은 빈 것이다 — DB NOT NULL 은 빈 문자열을 못 막는다", () => {
    const fields = { ...editableOf(CREATE.schedule), title: "   ", ends_on: "2026-10-25" };
    expect(missingFields(fields)).toEqual(["title"]);
  });

  it("막는 것이 여럿이면 다 낸다", () => {
    const fields = { ...editableOf(CREATE.schedule), title: "", starts_on: "" };
    expect(missingFields(fields)).toEqual(["title", "starts_on", "ends_on"]);
  });
});

describe("제출 본문", () => {
  it("시각 패턴은 그대로 되돌려 보내고 서버가 계산하는 scheduled_time 은 싣지 않는다", () => {
    const body = submitBodyOf(CREATE, { ...editableOf(CREATE.schedule), ends_on: "2026-10-25" });
    expect(body?.doses).toEqual([
      { timing_anchor: "breakfast", offset_minutes: 30, fixed_time: null },
      { timing_anchor: "dinner", offset_minutes: 30, fixed_time: null },
    ]);
    expect(JSON.stringify(body)).not.toContain("scheduled_time");
    // 🚨 화면 전용이거나 서버가 다시 계산하는 칸은 싣지 않는다.
    for (const key of ["draft_id", "notice_times", "missing", "before"]) {
      expect(body).not.toHaveProperty(key);
    }
  });

  it("복용 이유와 증상 코드는 초안 값 그대로 · 출처가 비면 한 줄 입력이다", () => {
    const body = submitBodyOf(CREATE, { ...editableOf(CREATE.schedule), ends_on: "2026-10-25" });
    expect(body?.schedule.indication_text).toBe("기침");
    expect(body?.schedule.symptom_codes).toEqual(["cough"]);
    expect(body?.source).toBe("utterance");
  });

  it("고르지 않은 선택 칸은 null 이고 적은 값은 앞뒤 공백을 뗀다", () => {
    const body = submitBodyOf(CREATE, {
      ...editableOf(CREATE.schedule),
      ends_on: "2026-10-25",
      note: "  밥 먹고 바로  ",
      dosage: " ",
    });
    expect(body?.schedule).toMatchObject({
      storage: null,
      med_form: null,
      note: "밥 먹고 바로",
      dosage: null,
    });
  });
});

describe("수정 초안의 달라지는 것", () => {
  const before = { schedule: { ...CREATE.schedule, ends_on: "2026-10-25" }, doses: PATTERN };

  it("알림 시각이 바뀌면 '19:30 → 20:00' 으로 선다", () => {
    const rows = medicationDiff(before, editableOf(before.schedule), ["08:30", "20:00"]);
    expect(rows).toEqual([{ term: "알림 시각", from: "08:30, 19:30", to: "08:30, 20:00" }]);
  });

  it("같으면 줄이 없다 — 보호자가 고친 칸만 더 선다", () => {
    const fields = editableOf(before.schedule);
    expect(medicationDiff(before, fields, ["08:30", "19:30"])).toEqual([]);
    const rows = medicationDiff(before, { ...fields, dosage: "5ml" }, ["08:30", "19:30"]);
    expect(rows).toEqual([{ term: "용량", from: "3ml", to: "5ml" }]);
  });
});

describe("제출 실패 — 다시 눌러도 되는가 (게이트 ㉡)", () => {
  it("동의 없음 · 없는 코스는 같은 답이라 잠근다", () => {
    expect(failureOf(new ApiError(403, "consent_required", "x")).final).toBe(true);
    expect(failureOf(new ApiError(404, "not_found", "x")).final).toBe(true);
  });

  it("형식 거절은 칸을 고치면 새 본문이라 잠그지 않는다", () => {
    expect(failureOf(new ApiError(400, "validation_failed", "x")).final).toBe(false);
  });

  it("🚨 같은 키가 처리 중(409)이면 '넣지 않았어요' 라고 하지 않고 잠그지 않는다 — 들어갔을 수 있다", () => {
    const failure = failureOf(new ApiError(409, "idempotency_in_progress", "x"));
    expect(failure.final).toBe(false);
    expect(failure.text).toContain("확인하지 못했어요");
    expect(failure.text).not.toContain("넣지 않았어요");
  });

  it("🚨 응답을 못 받은 실패는 단정하지 않고 같은 키로 다시 확인하게 한다", () => {
    for (const error of [
      new NetworkError(new Error("x")),
      new ApiError(503, "llm_unavailable", "x"),
    ]) {
      const failure = failureOf(error);
      expect(failure.final).toBe(false);
      expect(failure.text).not.toContain("넣지 않았어요");
    }
  });

  it("그 밖의 4xx 는 같은 답이라 잠근다", () => {
    expect(failureOf(new ApiError(403, "child_access_denied", "x")).final).toBe(true);
  });
});
