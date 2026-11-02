import { http, HttpResponse } from "msw";

import type {
  CheckDoseRequest,
  MedicationCourse,
  MedicationDraft,
  MedicationDose,
  MedicationsResponse,
  SubmitMedicationBody,
  SubmitMedicationResponse,
} from "@/lib/api/types";
import { toSeoulDateKey } from "@/lib/format";

import { currentScenario } from "../scenario";
import { apiError, networkDelay, url } from "./helpers";
import { withIdempotency } from "./idempotency";

/**
 * 복약 — 진행 중 코스 · 회차 체크 · 초안 제출 (#537).
 *
 * ⚠️ **경로와 모양이 잠정이다.** 서버 API 가 아직 없어서 화면이 먼저 잡은 계약을 흉내 낸다
 *    (`docs/web/medication-v1.md` §5). 서버가 생기면 Swagger 에 맞춰 여기와 `lib/api/types.ts` 를 같이 고친다.
 *    서버가 지킬 규칙은 `docs/medication/medication-tables-v1.md` §5 를 따랐다 — 지난 날 회차는 만들지 않고,
 *    아직 오지 않은 날의 회차는 체크할 수 없고, 수정해도 체크된 회차는 지우지 않는다.
 *
 * 🚨 **목만 날짜를 계산한다.** 화면은 서버가 준 `today` · `checkable` 을 쓴다 — 여기는 서버 흉내다.
 * 🚨 **약 이름은 지어낸 것이다** (최상위 §9 — 실제 처방 · 아이 정보를 넣지 않는다).
 */

/** childId → 진행 중 코스. 🚨 프로세스 수명만큼 산다 — `resetMedicationState()` 가 비운다. */
const courses = new Map<string, MedicationCourse[]>();
let seq = 0;

export function resetMedicationState(): void {
  courses.clear();
  seq = 0;
}

/** `YYYY-MM-DD` 에 날을 더한다 (목 전용 — 서버의 KST 날짜 흉내). */
function addDays(day: string, n: number): string {
  const [y, m, d] = day.split("-").map(Number);
  const at = new Date(Date.UTC(y, m - 1, d + n));
  return at.toISOString().slice(0, 10);
}

function todayKey(): string {
  return toSeoulDateKey(new Date());
}

/**
 * 코스의 회차 행. 🚨 **오늘까지만 싣는다** — 아직 오지 않은 날은 응답에 없다(잠정 계약). 지난 날도 `from` 앞은
 * 만들지 않는다 (시작을 과거로 당겨도 지난 회차는 만들지 않는다 · 테이블 문서 §5).
 */
function dosesFor(
  scheduleId: string,
  from: string,
  to: string,
  times: readonly string[],
): MedicationDose[] {
  const today = todayKey();
  const last = to < today ? to : today;
  const doses: MedicationDose[] = [];
  for (let day = from; day <= last; day = addDays(day, 1)) {
    for (const time of times) {
      doses.push({
        id: `${scheduleId}_${day}_${time}`,
        dose_date: day,
        scheduled_time: time,
        checked_at: null,
        checkable: true,
      });
    }
  }
  return doses;
}

/** 기본 상태 — 어제 시작해 내일 끝나는 시럽 하나. 어제 회차는 둘을 체크해 뒀다. */
function seeded(): MedicationCourse[] {
  if (currentScenario() === "empty") return [];
  const today = todayKey();
  const yesterday = addDays(today, -1);
  const times = ["08:30", "12:30", "19:30"];
  const id = "ms_seed";
  const doses = dosesFor(id, yesterday, today, times).map((dose) =>
    dose.dose_date === yesterday && dose.scheduled_time !== "19:30"
      ? { ...dose, checked_at: `${yesterday}T${dose.scheduled_time}:00+09:00` }
      : dose,
  );
  return [
    {
      id,
      title: "해열 시럽",
      dosage: "5ml",
      starts_on: yesterday,
      ends_on: addDays(today, 1),
      notice_times: times,
      doses,
    },
  ];
}

function coursesOf(childId: string): MedicationCourse[] {
  let list = courses.get(childId);
  if (!list) {
    list = seeded();
    courses.set(childId, list);
  }
  return list;
}

/**
 * 제출 본문의 회차 시각. 🚨 **목만 계산한다** — 서버는 DB 생성 컬럼이 계산한다. 기준 시각은 `core/medication_draft.py`
 * 의 `ANCHOR_TIMES` 와 같은 값이다 (저녁 19:00 · #546).
 */
const ANCHOR_MINUTES: Record<string, number> = {
  wake_up: 7 * 60 + 30,
  breakfast: 8 * 60,
  lunch: 12 * 60,
  dinner: 19 * 60,
  bedtime: 20 * 60 + 30,
};

function timesOf(doses: SubmitMedicationBody["doses"]): string[] {
  return doses
    .map((dose) => {
      if (dose.timing_anchor === "fixed") return dose.fixed_time ?? "";
      const minutes = ANCHOR_MINUTES[dose.timing_anchor] + dose.offset_minutes;
      return `${String(Math.floor(minutes / 60)).padStart(2, "0")}:${String(minutes % 60).padStart(2, "0")}`;
    })
    .sort();
}

/** DB CHECK 와 제출 API 가 막는 것 (테이블 문서 §4 · §5). 화면이 잠그지만 목도 같은 선에서 막는다. */
function invalid(body: Partial<SubmitMedicationBody>): string | null {
  const schedule = body.schedule;
  if (!schedule || typeof schedule.title !== "string" || schedule.title.trim() === "") {
    return "title";
  }
  if (!schedule.starts_on || !schedule.ends_on) return "ends_on";
  if (schedule.ends_on < schedule.starts_on) return "ends_on";
  if (!Array.isArray(body.doses) || body.doses.length === 0) return "doses";
  return null;
}

export const medicationHandlers = [
  http.get(url("/children/:cid/medications"), async ({ params }) => {
    await networkDelay();
    const body: MedicationsResponse = {
      today: todayKey(),
      courses: coursesOf(String(params.cid)),
    };
    return HttpResponse.json(body);
  }),

  // 🚨 승인 게이트 ㉡ — 키가 없으면 400. 같은 키 재시도는 처음 응답을 재생한다(코스가 두 개 생기지 않는다).
  http.post(
    url("/children/:cid/medications"),
    withIdempotency(async ({ params, request }) => {
      await networkDelay();
      const childId = String(params.cid);
      const body = (await request.json()) as Partial<SubmitMedicationBody>;
      const bad = invalid(body);
      if (bad || !body.schedule || !body.doses) {
        return apiError(400, "validation_failed", "요청 형식이 올바르지 않아요", {
          fields: [bad],
        });
      }
      const id = `ms_${++seq}`;
      const times = timesOf(body.doses);
      const today = todayKey();
      // 🚨 지난 날 회차는 만들지 않는다 — 시작이 과거여도 오늘부터다 (테이블 문서 §5).
      const from = body.schedule.starts_on < today ? today : body.schedule.starts_on;
      const course: MedicationCourse = {
        id,
        title: body.schedule.title.trim(),
        dosage: body.schedule.dosage,
        starts_on: body.schedule.starts_on,
        ends_on: body.schedule.ends_on,
        notice_times: times,
        doses: dosesFor(id, from, body.schedule.ends_on, times),
      };
      courses.set(childId, [...coursesOf(childId), course]);
      const response: SubmitMedicationResponse = { course };
      return HttpResponse.json(response, { status: 201 });
    }),
  ),

  // 🚨 승인 게이트 ㉡ — 수정. 체크된 회차는 지우지 않고, 미체크 회차만 새 시각으로 다시 만든다 (테이블 문서 §5).
  http.patch(
    url("/children/:cid/medications/:sid"),
    withIdempotency(async ({ params, request }) => {
      await networkDelay();
      const childId = String(params.cid);
      const list = coursesOf(childId);
      const current = list.find((c) => c.id === String(params.sid));
      if (!current) return apiError(404, "not_found", "복약 일정을 찾을 수 없어요");
      const body = (await request.json()) as Partial<SubmitMedicationBody>;
      const bad = invalid(body);
      if (bad || !body.schedule || !body.doses) {
        return apiError(400, "validation_failed", "요청 형식이 올바르지 않아요", {
          fields: [bad],
        });
      }
      const times = timesOf(body.doses);
      const today = todayKey();
      // 🚨 체크한 회차와 지난 날 회차는 그대로 둔다 — 다시 만드는 것은 아직 오지 않은 미체크 회차뿐이다.
      //    (목은 오늘 회차를 "아직 오지 않은" 쪽으로 친다. 시각까지 가르는지는 서버 몫이다.)
      const kept = current.doses.filter(
        (dose) => dose.checked_at !== null || dose.dose_date < today,
      );
      const keptSlots = new Set(kept.map((dose) => `${dose.dose_date} ${dose.scheduled_time}`));
      const from = body.schedule.starts_on < today ? today : body.schedule.starts_on;
      const remade = dosesFor(current.id, from, body.schedule.ends_on, times)
        // 🚨 남겨 둔 행과 같은 날 같은 시각이면 건너뛴다 (UNIQUE).
        .filter((dose) => !keptSlots.has(`${dose.dose_date} ${dose.scheduled_time}`));
      const course: MedicationCourse = {
        ...current,
        title: body.schedule.title.trim(),
        dosage: body.schedule.dosage,
        starts_on: body.schedule.starts_on,
        ends_on: body.schedule.ends_on,
        notice_times: times,
        doses: [...kept, ...remade].sort((a, b) =>
          `${a.dose_date} ${a.scheduled_time}`.localeCompare(`${b.dose_date} ${b.scheduled_time}`),
        ),
      };
      courses.set(
        childId,
        list.map((c) => (c.id === course.id ? course : c)),
      );
      const response: SubmitMedicationResponse = { course };
      return HttpResponse.json(response);
    }),
  ),

  /**
   * 회차 체크 · 해제. 🚨 **게이트가 아니다** — `withIdempotency` 로 감싸지 않는다(같은 상태를 두 번 보내도 같다).
   * 🚨 이미 체크된 회차를 다시 체크하면 처음 시각을 그대로 둔다 (09 준비물 체크와 같은 규칙).
   */
  http.put(url("/children/:cid/medication-doses/:did/check"), async ({ params, request }) => {
    await networkDelay();
    const childId = String(params.cid);
    const doseId = String(params.did);
    const list = coursesOf(childId);
    const found = list.flatMap((c) => c.doses).find((dose) => dose.id === doseId);
    if (!found) return apiError(404, "not_found", "회차를 찾을 수 없어요");
    if (!found.checkable) {
      return apiError(422, "validation_failed", "아직 체크할 수 없는 회차예요");
    }
    const { checked } = (await request.json()) as CheckDoseRequest;
    const next: MedicationDose = {
      ...found,
      checked_at: checked ? (found.checked_at ?? new Date().toISOString()) : null,
    };
    courses.set(
      childId,
      list.map((course) => ({
        ...course,
        doses: course.doses.map((dose) => (dose.id === doseId ? next : dose)),
      })),
    );
    return HttpResponse.json(next);
  }),
];

/* ── 04 대화 대본이 쓰는 복약 초안 (`runs.ts` 의 `med_draft`) ─────────────── */

/**
 * 한 run 이 내는 복약 초안 두 장 — 새 복약 하나 · 기본 코스(`ms_seed`)의 수정 하나.
 *
 * - 새 복약은 **기간이 비어 온다**(`missing: ["ends_on"]`). 발화에 며칠인지 없으면 서버가 짐작하지 않는다 — 카드가
 *   그 칸을 채우기 전에는 제출을 잠가야 한다 (#537 의 가장 흔한 경우).
 * - 수정은 저녁 회차가 19:30 → 20:00 으로 바뀐다. 카드가 "19:30 → 20:00" 을 그려야 한다.
 * 🚨 시각은 서버 계산 결과를 흉내 낸 값이다 (아침 08:00 · 점심 12:00 · 저녁 19:00 + 30분).
 */
export function medicationRunDrafts(): MedicationDraft[] {
  const today = todayKey();
  const pattern = [
    { timing_anchor: "breakfast", offset_minutes: 30, fixed_time: null, scheduled_time: "08:30" },
    { timing_anchor: "lunch", offset_minutes: 30, fixed_time: null, scheduled_time: "12:30" },
    { timing_anchor: "dinner", offset_minutes: 30, fixed_time: null, scheduled_time: "19:30" },
  ] satisfies MedicationDraft["doses"];
  return [
    {
      draft_id: "md_create",
      op: "create",
      source: "utterance",
      schedule: {
        title: "기침 시럽",
        dosage: "3ml",
        indication_text: "기침",
        storage: null,
        med_form: null,
        note: null,
        symptom_codes: ["cough"],
        starts_on: today,
        ends_on: null,
      },
      doses: pattern,
      notice_times: ["08:30", "12:30", "19:30"],
      missing: ["ends_on"],
    },
    {
      draft_id: "md_update",
      op: "update",
      source: "utterance",
      schedule_id: "ms_seed",
      schedule: {
        title: "해열 시럽",
        dosage: "5ml",
        indication_text: "열",
        storage: null,
        med_form: null,
        note: null,
        symptom_codes: ["fever"],
        starts_on: addDays(today, -1),
        ends_on: addDays(today, 1),
      },
      doses: [
        pattern[0],
        pattern[1],
        { timing_anchor: "fixed", offset_minutes: 0, fixed_time: "20:00", scheduled_time: "20:00" },
      ],
      notice_times: ["08:30", "12:30", "20:00"],
      missing: [],
      before: {
        schedule: {
          title: "해열 시럽",
          dosage: "5ml",
          indication_text: "열",
          storage: null,
          med_form: null,
          note: null,
          symptom_codes: ["fever"],
          starts_on: addDays(today, -1),
          ends_on: addDays(today, 1),
        },
        doses: pattern,
      },
    },
  ];
}

/** 복약 중단 확인에 '네' 라고 답했다 — 코스를 목록에서 뺀다 (`status='stopped'` 는 진행 중이 아니다). */
export function stopSeededCourse(childId: string): void {
  courses.set(
    childId,
    coursesOf(childId).filter((course) => course.id !== "ms_seed"),
  );
}
