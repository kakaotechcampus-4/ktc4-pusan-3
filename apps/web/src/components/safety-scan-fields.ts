import type { SafetyScanCandidate } from "@/lib/api";
import { isSeverityFor } from "@/lib/health-safety";

/**
 * 11-2 검사지 확인 화면이 쓰는 **한 줄의 모양과 분류**.
 *
 * 🚨 **확인 화면(`safety-scan-review`)과 고치기 시트(`safety-scan-row-sheet`)가 같은 값을
 *    본다.** 양쪽에 따로 두면 목록이 "고를 수 있다" 고 판단한 줄을 시트가 "아직 아니다" 라고
 *    보는 어긋남이 생긴다 — 승인 게이트에서 그 어긋남은 **확인 안 한 것이 등록되는** 경로다.
 *
 * 🚨 **심각도 선택지는 직접 적기(`HealthSafetySheet`)와 같은 곳(`lib/health-safety.ts`)에서
 *    온다.** 같은 기록을 두 경로로 넣는데 문구가 갈리면 목록에서 같은 것이 둘로 보인다.
 */

/** 등록할 때의 한 줄. 서버가 준 후보 + 보호자가 채운 것 + 등록 결과. */
export interface ScanRow {
  id: string;
  label: string;
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

  return {
    id: candidate.id,
    label,
    // 🚨 검사지는 전부 알레르기라 심각도는 검사 Class 다. 쓸 수 없는 값이 오면 비운다 — DB 가
    //    막는 조합을 보내서 알레르기 등록이 통째로 실패하는 것보다, 심각도가 빈 채 등록되는 편이
    //    안전하다 (필터는 심각도를 쓰지 않는다).
    severity:
      candidate.severity !== null && isSeverityFor("allergy", candidate.severity)
        ? candidate.severity
        : null,
    reactions: candidate.reactions,
    sourceText: candidate.source_text,
    alreadyRegistered: false,
    confirmed: false,
    // 🚨 **미리 고르는 조건이 좁다.** 필수 칸이 다 있고 · 원문이 있는 줄만. 하나라도 어긋나면
    //    보호자가 직접 골라야 한다. (이미 등록된 줄은 화면이 따로 걸러 낸다 — 위 머리말.)
    checked: label !== "" && candidate.source_text !== null,
    status: "idle",
  };
}

/** 필수 칸이 다 찼는가. 심각도·증상은 없어도 등록된다 (추측해 채우지 않는다). */
export function isComplete(row: ScanRow): boolean {
  return row.label.trim() !== "";
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

/**
 * **승인 목록에 넣을 수 있는 줄인가.** 확인이 필요한 줄은 ㉠ · ㉡ **둘 다** 고를 수 없다.
 *
 * 🚨 `isComplete` 만 보면 ㉡ 이 샌다 — 칸이 다 찬 원문 없는 줄이 체크 한 번으로 승인 목록에
 *    들어가고, 요약은 여전히 "확인하지 않은 N건은 등록하지 않아요" 라고 말했다. 원문 대조 없이
 *    알레르기가 확정되는 길이었다 (#242).
 */
export function isSelectable(row: ScanRow): boolean {
  return isComplete(row) && !needsReview(row);
}

/** 왜 확인이 필요한지. 🚨 두 사유가 섞이지 않게 **하나만** 고른다. */
export function reviewReason(row: ScanRow): string {
  if (row.label.trim() === "") return "이름을 읽지 못했어요. 검사지를 보고 채워주세요.";
  return "검사지 원문을 읽지 못했어요. 검사지를 보고 확인해주세요.";
}
