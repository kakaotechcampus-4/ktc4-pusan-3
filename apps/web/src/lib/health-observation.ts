/**
 * 건강 관찰 한 행을 줄로 그릴 때 쓰는 글. 04 대화 결과 · 07 기록 목록 · 기록 상세가 같은 글을 쓴다.
 *
 * 🚨 **한 행 = 한 사건이다** (#551) — 증상 행이거나 먹인 약 행이다. 같은 말에서 함께 저장된 두 행은
 *    `raw_text` 가 같아서, 증상이 빈 약 행을 원문으로 그리면 같은 줄이 두 번 보인다. 그래서 약 행은
 *    약 이름이 제목을 받는다.
 * 🚨 값은 보호자가 말한 것을 서버가 담은 그대로다. 체온은 숫자와 잰 부위만 옮긴다 — 정상 범위 ·
 *    높다/낮다 · 색으로 평가하지 않는다 (최상위 CLAUDE.md §2 · Health 는 진단하지 않는다).
 */
import type { MeasureSite, ObservationHealth } from "@/lib/api/types";

const SITE_LABEL: Record<MeasureSite, string> = {
  ear: "귀",
  forehead: "이마",
  armpit: "겨드랑이",
  oral: "입",
  rectal: "항문",
};

type HealthFields = ObservationHealth["domain_fields"];

/** 먹인 약 행인가 — `medication_given` 이 있으면 그 행은 증상이 아니라 먹인 약이다. */
export function isMedicationRow(fields: HealthFields): boolean {
  return typeof fields.medication_given === "string" && fields.medication_given.length > 0;
}

/**
 * 그 행이 무엇에 관한 것인가. 증상 행은 증상, 약 행은 "해열제 (먹인 약)". 둘 다 없으면 `null` —
 * 부르는 쪽이 원문을 쓴다(증상이 빈 옛 기록).
 */
export function healthTitle(fields: HealthFields): string | null {
  if (isMedicationRow(fields)) return `${fields.medication_given} (먹인 약)`;
  const symptoms = fields.symptom;
  return symptoms && symptoms.length > 0 ? symptoms.join(", ") : null;
}

/**
 * 줄에 붙일 시각(ISO). 본 시각(`observed_time`)이 먼저고, 없으면 잰 시각(`measured_at`)이다 —
 * 체온 행은 잰 시각만 있는 일이 많아서, 같은 날 두 번 잰 기록이 시각 없이 같은 줄로 겹친다.
 */
export function healthMoment(fields: HealthFields): string | null {
  return fields.observed_time ?? fields.measured_at ?? null;
}

/** "38.5℃ (귀)". 체온이 없으면 `null`. 숫자는 소수 첫째 자리까지 그대로 쓴다. */
export function temperatureLabel(fields: HealthFields): string | null {
  if (typeof fields.temperature !== "number") return null;
  const site = fields.measure_site ? SITE_LABEL[fields.measure_site] : null;
  const value = `${fields.temperature.toFixed(1)}℃`;
  return site ? `${value} (${site})` : value;
}
