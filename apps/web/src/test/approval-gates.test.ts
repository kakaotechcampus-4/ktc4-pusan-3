import { readdirSync, readFileSync } from "node:fs";
import { join, relative } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

/**
 * 승인 게이트가 **딱 2곳**인 것을 코드로 건다 (최상위 CLAUDE.md §2 실행 · #242).
 *
 *   ㉠ 캘린더 쓰기 — 초안 제출 (`submitEventDraft` · `updateEventDraft`)
 *   ㉡ 건강·알레르기 기록 확정 — `addHealthSafety`
 *
 * 브라우저 테스트(`e2e/`)는 "이 화면에서 뜬다" 는 보여도 "다른 곳에서는 안 뜬다" 는 모든 경로를
 * 돌아야 증명된다. 그래서 **어디서 부를 수 있는가**를 파일 목록으로 고정한다.
 *
 * 🚨 이 테스트가 깨졌다면 목록을 고치기 전에 묻는다 — 게이트를 **늘리는** 변경인가?
 *    늘리지도 줄이지도 않는다 (§2). 같은 게이트를 다른 화면으로 옮긴 것이면 목록을 고치고,
 *    그 이유를 PR 에 적는다 (11-2 가 시트에서 화면으로 옮겨 온 것처럼 · apps/web/CLAUDE.md §3).
 */

const SRC = fileURLToPath(new URL("..", import.meta.url));

/** 주석을 뺀 소스. 주석에 규칙을 설명하느라 토큰 이름을 적는 일이 많다. */
const sources: Map<string, string> = new Map(
  readdirSync(SRC, { recursive: true, encoding: "utf8" })
    .filter((path) => /\.(ts|tsx)$/.test(path) && !/\.test\.tsx?$/.test(path))
    .map((path) => [
      relative(SRC, join(SRC, path)).replaceAll("\\", "/"),
      readFileSync(join(SRC, path), "utf8")
        .replace(/\/\*[\s\S]*?\*\//g, "")
        .replace(/(^|[^:"'`])\/\/.*$/gm, "$1"),
    ]),
);

function filesMatching(pattern: RegExp): string[] {
  return [...sources]
    .filter(([, code]) => pattern.test(code))
    .map(([path]) => path)
    .sort();
}

/** 정의하는 곳 — 부르는 곳이 아니다. */
const DEFINED_IN = "lib/api/operations.ts";

/** 내부 문서 화면. 토큰과 컴포넌트를 전부 늘어놓고 보여 주는 곳이라 게이트 표식이 다 있다. */
const DESIGN_SYSTEM = /^app\/design-system\//;

const callers = (fn: string) =>
  filesMatching(new RegExp(`\\b${fn}\\(`)).filter((path) => path !== DEFINED_IN);

describe("승인 게이트 ㉠ 캘린더 쓰기", () => {
  it("초안 제출은 초안 카드 목록(05 · 04)과 08 일정 넣기 시트에서만 부른다", () => {
    expect(callers("submitEventDraft")).toEqual([
      "components/event-draft-list.tsx",
      "components/photo-entry-sheet.tsx",
    ]);
    expect(callers("updateEventDraft")).toEqual(["components/event-draft-list.tsx"]);
  });
});

describe("승인 게이트 ㉡ 건강·알레르기 확정", () => {
  it("등록은 05 재료 확인 · 02/11 직접 적기 · 11-2 검사지에서만 부른다", () => {
    expect(callers("addHealthSafety")).toEqual([
      "app/child/[childId]/profile/safety-scan/page.tsx",
      "components/health-safety-list.tsx",
      "components/safety-check-sheet.tsx",
    ]);
  });
});

describe("게이트 표식은 게이트 안에만", () => {
  it("approve 버튼은 게이트를 그리는 컴포넌트에만 있다", () => {
    expect(filesMatching(/variant="approve"/).filter((path) => !DESIGN_SYSTEM.test(path))).toEqual([
      "components/event-draft-card.tsx",
      "components/health-safety-list.tsx",
      "components/photo-entry-sheet.tsx",
      "components/safety-scan-review.tsx",
    ]);
  });

  /** caution 은 승인 게이트 2곳 전용이다 (apps/web/CLAUDE.md §5). */
  const CAUTION = /tone="caution"|\b(?:bg|text|border(?:-[lrtb])?)-caution(?:-soft|-ink)?\b/;
  const CAUTION_ALLOWED = [
    "components/event-draft-card.tsx",
    "components/health-safety-list.tsx",
    "components/photo-entry-sheet.tsx",
    "components/safety-check-sheet.tsx",
    "components/safety-scan-review.tsx",
    // primitive — tone 을 받아 그리기만 한다.
    "components/ui/banner.tsx",
  ];
  // 한동안 05 의 guard 배너("식사 제안을 만들지 않았어요")가 caution 이었다 (#242). "막혔다" 는 danger 다.
  it("caution 색은 게이트를 그리는 컴포넌트에만 쓴다", () => {
    expect(filesMatching(CAUTION).filter((path) => !DESIGN_SYSTEM.test(path))).toEqual(
      CAUTION_ALLOWED,
    );
  });

  it("approve 높이 토큰은 Button primitive 한 곳에서만 쓴다", () => {
    expect(filesMatching(/\bmin-h-approve\b/)).toEqual(["components/ui/button.tsx"]);
  });

  it("실수로 닫히지 않는 시트는 게이트 시트뿐이다", () => {
    expect(filesMatching(/dismissible=\{false\}/)).toEqual([
      "components/approval-sheet.tsx",
      "components/health-safety-list.tsx",
      "components/safety-check-sheet.tsx",
    ]);
  });
});
