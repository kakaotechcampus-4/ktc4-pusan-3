import type {
  MedicationDraft,
  MedicationForm,
  MedicationScheduleDraft,
  MedicationStorage,
  SubmitMedicationBody,
} from "@/lib/api/types";
import { isApiError } from "@/lib/api/errors";
import { formatDay } from "@/lib/format";

/**
 * 복약 초안 카드의 판단 (#537 · `docs/web/medication-v1.md`). 카드(`components/medication-draft-card.tsx`)는 그리기만 한다.
 *
 * 🚨 **시각을 계산하지 않는다.** 회차 시각 · 알림 시각은 서버가 기준 시각 + 오프셋으로 만든 값이고(#546 에서 저녁
 *    기준이 바뀐 것처럼 기준은 서버 상수다), 여기는 그 값을 비교하거나 되돌려 보낼 뿐이다.
 */

/** 카드에서 보호자가 고칠 수 있는 칸. 🚨 `indication_text` · `symptom_codes` 는 없다 — 아래 `editableOf`. */
export interface MedicationFields {
  title: string;
  dosage: string;
  storage: MedicationStorage | "";
  med_form: MedicationForm | "";
  note: string;
  /** `YYYY-MM-DD` 또는 빈 문자열. 🚨 비었으면 오늘로 채우지 않는다. */
  starts_on: string;
  ends_on: string;
}

/**
 * 초안을 카드 칸으로 옮긴다. `null` 은 빈 문자열이다 — 입력 칸이 다루는 값이다.
 *
 * 🚨 **복용 이유(`indication_text`)는 고치는 칸이 아니다.** 서버가 그 원문으로 증상 코드를 뽑아 같이 실어 보냈다 —
 *    이유만 고치면 코드와 문장이 어긋난 채 저장된다. 다르면 대화로 다시 말한다.
 */
export function editableOf(schedule: MedicationScheduleDraft): MedicationFields {
  return {
    title: schedule.title ?? "",
    dosage: schedule.dosage ?? "",
    storage: schedule.storage ?? "",
    med_form: schedule.med_form ?? "",
    note: schedule.note ?? "",
    starts_on: schedule.starts_on ?? "",
    ends_on: schedule.ends_on ?? "",
  };
}

/** 비어서 제출을 막는 필수 칸. 🚨 초안의 `missing` 이 아니라 **지금 칸의 값**으로 본다 — 채우면 풀린다. */
export type MissingField = "title" | "starts_on" | "ends_on";

export function missingFields(fields: MedicationFields): MissingField[] {
  const missing: MissingField[] = [];
  if (fields.title.trim() === "") missing.push("title");
  if (fields.starts_on === "") missing.push("starts_on");
  if (fields.ends_on === "") missing.push("ends_on");
  return missing;
}

/** 막힌 이유 한 줄씩. 🚨 막는 것이 둘이면 둘 다 말한다 (`EventDraftCard` 와 같은 규칙). */
export const MISSING_REASON: Record<MissingField, string> = {
  title: "약 이름을 적어주셔야 넣을 수 있어요.",
  starts_on: "언제부터 먹이는지 골라주셔야 넣을 수 있어요.",
  ends_on: "언제까지 먹이는지 골라주셔야 넣을 수 있어요. 기간이 정해진 약만 넣을 수 있어요.",
};

const trimmedOrNull = (value: string) => (value.trim() === "" ? null : value.trim());

/**
 * 제출 본문. 🚨 필수 칸이 비었으면 만들지 않는다(`null`) — 카드가 버튼을 잠그는 것과 같은 판정이다.
 *
 * - `doses` 는 초안의 시각 패턴을 **그대로** 되돌려 보낸다. `scheduled_time` 은 빼고(서버 생성 컬럼) 나머지 셋만.
 * - `indication_text` · `symptom_codes` 도 초안 값 그대로다 (위 `editableOf`).
 */
export function submitBodyOf(
  draft: MedicationDraft,
  fields: MedicationFields,
): SubmitMedicationBody | null {
  if (missingFields(fields).length > 0) return null;
  return {
    schedule: {
      title: fields.title.trim(),
      dosage: trimmedOrNull(fields.dosage),
      indication_text: draft.schedule.indication_text,
      storage: fields.storage === "" ? null : fields.storage,
      med_form: fields.med_form === "" ? null : fields.med_form,
      note: trimmedOrNull(fields.note),
      symptom_codes: draft.schedule.symptom_codes,
      starts_on: fields.starts_on,
      ends_on: fields.ends_on,
    },
    doses: draft.doses.map(({ timing_anchor, offset_minutes, fixed_time }) => ({
      timing_anchor,
      offset_minutes,
      fixed_time,
    })),
    // 처방전 경로(`prescription_ocr`)가 생기기 전에는 늘 한 줄 입력이다. 서버가 `null` 로 낸 것도 그것이다.
    source: draft.source ?? "utterance",
  };
}

export const STORAGE_LABEL: Record<MedicationStorage, string> = {
  room: "실온",
  fridge: "냉장",
};

export const MED_FORM_LABEL: Record<MedicationForm, string> = {
  liquid: "물약",
  powder: "가루약",
  tablet: "알약",
  ointment: "연고",
  drops: "점안 · 점이액",
};

/** 시각 목록 한 줄. 서버 값(`HH:MM`)을 그대로 잇는다 — 다시 계산하거나 오전 · 오후로 바꾸지 않는다. */
export function timesLabel(times: readonly string[]): string {
  return times.join(", ");
}

/** 날짜 칸 표시. 비었으면 "없음" — 오늘로 채우지 않는다. */
function dayOr(value: string): string {
  return value === "" ? "없음" : formatDay(value);
}

export interface DiffRow {
  term: string;
  from: string;
  to: string;
}

/**
 * 수정 초안에서 **무엇이 달라지는지**. 🚨 보호자가 보고 싶은 것은 "19:30 → 20:00" 처럼 값의 변화다 (#537).
 * 알림 시각은 보호자가 고칠 수 없는 칸이라 초안의 `notice_times` 와 원본 회차 시각을 비교한다.
 */
export function medicationDiff(
  before: NonNullable<MedicationDraft["before"]>,
  fields: MedicationFields,
  noticeTimes: readonly string[],
): DiffRow[] {
  const rows: DiffRow[] = [];
  const was = editableOf(before.schedule);
  const empty = "없음";

  if (was.title.trim() !== fields.title.trim()) {
    rows.push({ term: "약 이름", from: was.title || empty, to: fields.title.trim() || empty });
  }
  if (was.dosage.trim() !== fields.dosage.trim()) {
    rows.push({ term: "용량", from: was.dosage || empty, to: fields.dosage.trim() || empty });
  }
  const beforeTimes = before.doses.map((d) => d.scheduled_time).sort();
  if (timesLabel(beforeTimes) !== timesLabel(noticeTimes)) {
    rows.push({ term: "알림 시각", from: timesLabel(beforeTimes), to: timesLabel(noticeTimes) });
  }
  if (was.starts_on !== fields.starts_on) {
    rows.push({ term: "시작", from: dayOr(was.starts_on), to: dayOr(fields.starts_on) });
  }
  if (was.ends_on !== fields.ends_on) {
    rows.push({ term: "끝", from: dayOr(was.ends_on), to: dayOr(fields.ends_on) });
  }
  if (was.storage !== fields.storage) {
    rows.push({
      term: "보관",
      from: was.storage ? STORAGE_LABEL[was.storage] : empty,
      to: fields.storage ? STORAGE_LABEL[fields.storage] : empty,
    });
  }
  if (was.med_form !== fields.med_form) {
    rows.push({
      term: "약 모양",
      from: was.med_form ? MED_FORM_LABEL[was.med_form] : empty,
      to: fields.med_form ? MED_FORM_LABEL[fields.med_form] : empty,
    });
  }
  if (was.note.trim() !== fields.note.trim()) {
    rows.push({ term: "특이사항", from: was.note || empty, to: fields.note.trim() || empty });
  }
  return rows;
}

/**
 * 제출 실패의 말과, 다시 눌러도 되는가 (`api-client-v1.md` §3). 🚨 **실패를 한 덩어리로 뭉뚱그리지 않는다** — 다시 눌러야
 * 하는 것과 눌러도 같은 답이 오는 것은 다른 말이다 (`health-safety-list.tsx` 와 같은 판단).
 *
 * - `consent_required` — 동의 전이라 아무것도 넣지 않았다. 다시 눌러도 같다. 동의 화면은 아직 없다.
 * - `not_found` — 그 사이 멈췄거나 바뀐 코스다(수정 초안). 다시 눌러도 같은 404 다.
 * - `validation_failed` — 같은 본문은 같은 답이다. 칸을 고치면 본문이 바뀌어 새 키로 다시 나간다 — 잠그지 않는다.
 * - 🚨 `idempotency_in_progress`(409) — **같은 키의 첫 요청이 아직 처리 중이다.** 들어갔을 수 있어서 "넣지 않았어요" 가
 *   아니고, 잠그지도 않는다 — 같은 키로 다시 누르면 처음 응답이 재생돼 결과를 확인한다 (`idempotency-v1.md` 표).
 * - 그 밖의 4xx — 같은 본문이면 같은 답이다. 잠근다.
 * - 응답을 못 받음(네트워크 · 5xx) — 서버가 받았을 수 있다. 단정하지 않고 같은 키로 다시 확인하게 한다.
 * ⚠️ 서버 코드가 정해지기 전이라 잠정이다 (`docs/web/medication-v1.md` §6).
 */
export function failureOf(error: unknown): { text: string; final: boolean } {
  if (isApiError(error, "consent_required")) {
    return {
      text: "건강 정보 동의를 받기 전이라 넣은 것은 하나도 없어요. 동의 화면은 아직 없어요.",
      final: true,
    };
  }
  if (isApiError(error, "not_found")) {
    return {
      text: "이미 멈췄거나 바뀐 복약 일정이라 넣지 않았어요. 바꾸려면 대화에서 다시 말씀해 주세요.",
      final: true,
    };
  }
  if (isApiError(error, "validation_failed")) {
    return {
      text: "넣을 수 없는 값이 있어서 넣지 않았어요. 약 이름과 기간을 확인해 주세요.",
      final: false,
    };
  }
  if (isApiError(error) && error.status < 500 && !isApiError(error, "idempotency_in_progress")) {
    return { text: "넣지 않았어요. 다시 눌러도 같은 결과가 와요.", final: true };
  }
  return {
    text: "넣었는지 확인하지 못했어요. 다시 누르면 같은 요청으로 확인해요. 이미 들어갔다면 두 번 들어가지 않아요.",
    final: false,
  };
}
