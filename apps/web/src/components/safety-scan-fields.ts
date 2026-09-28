import type { SafetyScanCandidate } from "@/lib/api";

/**
 * 11-2 검사지 확인 화면이 쓰는 **한 줄의 모양과 분류**.
 *
 * 🚨 **확인 화면(`safety-scan-review`)과 고치기 시트(`safety-scan-row-sheet`)가 같은 값을
 *    본다.** 양쪽에 따로 두면 목록이 "고를 수 있다" 고 판단한 줄을 시트가 "아직 아니다" 라고
 *    보는 어긋남이 생긴다 — 승인 게이트에서 그 어긋남은 **확인 안 한 것이 등록되는** 경로다.
 *
 * 🚨 **직접 적기(`HealthSafetySheet`)와 선택지 문구를 맞춘다.** 같은 기록을 두 경로로 넣는데
 *    한쪽만 "음식", 다른 쪽만 "식품" 이면 목록에서 같은 것이 둘로 보인다.
 */

/** 🚨 `""`(못 읽음)이 첫 줄이다 — 못 읽은 칸을 기본값으로 메우지 않는다 (최상위 §2). */
export const CATEGORY_OPTIONS = [
  { value: "", label: "고르지 않음" },
  { value: "식품", label: "음식" },
  { value: "약", label: "약" },
  { value: "환경", label: "환경 · 계절" },
  { value: "기타", label: "그 밖에" },
] as const;

/**
 * 🚨 **"모르겠어요" 가 기본값이다.** 보호자가 안 고르면 `severity` 를 보내지 않는다 —
 *    심각도는 추측하면 안 되는 값이다 (NF-03 · 직접 적기와 같은 규칙).
 */
export const SEVERITY_OPTIONS = [
  { value: "unknown", label: "모르겠어요" },
  { value: "mild", label: "가볍게" },
  { value: "moderate", label: "보통" },
  { value: "severe", label: "심하게" },
] as const;

export const SEVERITY_LABEL: Record<string, string> = {
  mild: "가볍게",
  moderate: "보통",
  severe: "심하게",
};

/** 등록할 때의 한 줄. 서버가 준 후보 + 보호자가 채운 것 + 등록 결과. */
export interface ScanRow {
  id: string;
  label: string;
  category: string;
  severity: string | null;
  reactions: string[];
  sourceText: string | null;
  /**
   * 이미 등록되어 있는 항목. 고를 수 없다 — 승인하고 나서 409 를 보는 일이 없게.
   * 🚨 **화면이 렌더마다 채운다** (`toRow` 는 `false` 로 둔다). 왜인지는 그 함수 머리말에.
   */
  alreadyRegistered: boolean;
  /** 보호자가 고치기 시트에서 확인을 눌렀다. 🚨 원문이 없는 줄은 이것만이 대조를 대신한다. */
  confirmed: boolean;
  checked: boolean;
  status: "idle" | "saving" | "done" | "failed";
}

/**
 * 🚨 **`alreadyRegistered` 를 여기서 굳히지 않는다.** 등록 목록은 검사지를 읽는 동안에도
 *    도착하고(따로 부르는 쿼리다), 이 화면에서 한 줄 등록할 때마다 바뀐다. 읽는 순간의
 *    스냅샷으로 굳히면 **아직 안 온 목록 = 빈 목록**이라 이미 있는 항목이 고를 수 있는 줄로
 *    서고, 승인하고 나서 409 를 본다. 화면이 렌더마다 지금 목록으로 다시 판단한다.
 */
export function toRow(candidate: SafetyScanCandidate): ScanRow {
  const label = candidate.label ?? "";
  const category = candidate.category ?? "";

  return {
    id: candidate.id,
    label,
    category,
    severity: candidate.severity,
    reactions: candidate.reactions,
    sourceText: candidate.source_text,
    alreadyRegistered: false,
    confirmed: false,
    // 🚨 **미리 고르는 조건이 좁다.** 필수 칸이 다 있고 · 원문이 있는 줄만. 하나라도 어긋나면
    //    보호자가 직접 골라야 한다. (이미 등록된 줄은 화면이 따로 걸러 낸다 — 위 머리말.)
    checked: label !== "" && category !== "" && candidate.source_text !== null,
    status: "idle",
  };
}

/** 필수 칸이 다 찼는가. 심각도·증상은 없어도 등록된다 (추측해 채우지 않는다). */
export function isComplete(row: ScanRow): boolean {
  return row.label.trim() !== "" && row.category !== "";
}

/**
 * **확인이 필요한 줄인가.** 두 가지가 여기 걸린다 —
 *
 *   ㉠ **필수 칸을 못 읽었다.** 채우기 전에는 고를 수도 없다.
 *   ㉡ **검사지 원문을 못 읽었다.** 칸은 다 찼지만 **무엇을 보고 옮겼는지가 없다.** 대조할
 *      것이 없는 승인은 확인이 아니라서, 보호자가 고치기 시트에서 한 번 보고 확인해야
 *      비로소 "잘 읽은 것" 으로 내려간다 (`confirmed`).
 *
 * 🚨 **이미 등록된 줄은 여기 오지 않는다.** 할 일이 없는 줄이라 확인 목록에 섞으면 정작
 *    손봐야 할 줄이 그만큼 묻힌다.
 */
export function needsReview(row: ScanRow): boolean {
  if (row.alreadyRegistered) return false;
  if (!isComplete(row)) return true;
  return row.sourceText === null && !row.confirmed;
}

/** 왜 확인이 필요한지. 🚨 두 사유가 섞이지 않게 **하나만** 고른다. */
export function reviewReason(row: ScanRow): string {
  if (row.label.trim() === "" && row.category === "") {
    return "이름과 분류를 읽지 못했어요. 검사지를 보고 채워주세요.";
  }
  if (row.label.trim() === "") return "이름을 읽지 못했어요. 검사지를 보고 채워주세요.";
  if (row.category === "") return "분류를 읽지 못했어요. 무엇에 대한 것인지 골라주세요.";
  return "검사지 원문을 읽지 못했어요. 검사지를 보고 확인해주세요.";
}
