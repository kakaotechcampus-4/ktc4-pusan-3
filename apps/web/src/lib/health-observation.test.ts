import { describe, expect, it } from "vitest";

import type { ObservationHealth } from "@/lib/api/types";
import {
  healthMoment,
  healthTitle,
  isMedicationRow,
  temperatureLabel,
} from "@/lib/health-observation";

type Fields = ObservationHealth["domain_fields"];

describe("건강 관찰 줄의 글 (#551)", () => {
  it("증상 행은 증상이 제목이다", () => {
    const fields: Fields = { symptom: ["발열", "기침"] };

    expect(healthTitle(fields)).toBe("발열, 기침");
    expect(isMedicationRow(fields)).toBe(false);
  });

  it("먹인 약 행은 약 이름이 제목이다 — 같은 원문의 증상 행과 줄이 겹치지 않는다", () => {
    const fields: Fields = { symptom: [], medication_given: "해열제" };

    expect(healthTitle(fields)).toBe("해열제 (먹인 약)");
    expect(isMedicationRow(fields)).toBe(true);
  });

  it("증상도 약도 없는 옛 행은 null — 부르는 쪽이 원문을 쓴다", () => {
    expect(healthTitle({ symptom: [] })).toBeNull();
  });

  it("시각은 본 시각이 먼저고, 없으면 잰 시각이다 — 체온 행이 시각 없이 겹치지 않는다", () => {
    const night = "2026-10-22T23:00:00+09:00";
    const morning = "2026-10-23T07:00:00+09:00";

    expect(healthMoment({ temperature: 38.5, measured_at: night })).toBe(night);
    expect(healthMoment({ observed_time: morning, measured_at: night })).toBe(morning);
    expect(healthMoment({ observed_time: null, measured_at: null })).toBeNull();
  });

  it("체온은 숫자와 잰 부위만 옮긴다", () => {
    expect(temperatureLabel({ temperature: 38.5, measure_site: "ear" })).toBe("38.5℃ (귀)");
    expect(temperatureLabel({ temperature: 38 })).toBe("38.0℃");
    expect(temperatureLabel({ symptom: ["기침"] })).toBeNull();
  });

  it("체온에 평가 표현을 붙이지 않는다", () => {
    const label = temperatureLabel({ temperature: 39.5, measure_site: "armpit" }) ?? "";

    for (const word of ["정상", "높", "낮", "고열", "미열", "위험"]) {
      expect(label).not.toContain(word);
    }
  });
});
