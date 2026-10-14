import { readdirSync, readFileSync } from "node:fs";
import { join, relative } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

/**
 * 움직임 규칙 중 **코드 모양으로 걸 수 있는 것**을 건다 (디자인 시스템 §8 · #426).
 * 실제로 움직이는지는 `e2e/motion.spec.ts` 가 브라우저에서 본다.
 *
 * 🚨 여기가 깨졌다면 목록을 고치기 전에 묻는다 — 승인 게이트 안에 움직임을 넣는 변경인가?
 *    게이트 안에서는 색 변화 말고 움직이지 않는다 (§8 "하지 않는 것").
 */

const SRC = fileURLToPath(new URL("..", import.meta.url));

/** 주석을 뺀 소스. 주석에 규칙을 설명하느라 토큰 이름을 적는 일이 많다 (`approval-gates.test.ts` 와 같다). */
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

/** 내부 문서 화면. 컴포넌트를 전부 늘어놓고 보여 주는 곳이다. */
const DESIGN_SYSTEM = /^app\/design-system\//;

describe("승인 게이트 안에는 움직임이 없다", () => {
  it("게이트 버튼을 담은 시트만 `gate` 로 열고 닫는 움직임을 끈다", () => {
    // JSX 의 값 없는 prop 한 줄 (`gate`). 시트를 새로 만들 때 게이트가 안에 있으면 여기 더한다.
    expect(filesMatching(/^\s*gate\s*$/m).filter((path) => !DESIGN_SYSTEM.test(path))).toEqual([
      "app/child/[childId]/chat/page.tsx",
      "components/approval-sheet.tsx",
      "components/health-safety-list.tsx",
      "components/photo-entry-sheet.tsx",
      "components/safety-check-sheet.tsx",
    ]);
  });

  it("게이트 확정을 부르는 파일의 시트는 전부 `gate` 다", () => {
    const confirming = filesMatching(/\b(submitEventDraft|addHealthSafety|updateHealthSafety)\(/)
      .filter((path) => path !== "lib/api/operations.ts")
      .filter((path) => /<BottomSheet\b/.test(sources.get(path) ?? ""));
    for (const path of confirming) {
      expect(sources.get(path), `${path} 의 시트가 게이트인데 움직인다`).toMatch(/^\s*gate\s*$/m);
    }
  });

  it("11-2 검사지 확인(게이트 ㉡)의 자리 옮김은 새 줄을 페이드하지 않는다", () => {
    expect(sources.get("components/safety-scan-review.tsx")).toMatch(
      /useFlip<[^>]*>\(\{\s*fadeIn:\s*false\s*\}\)/,
    );
  });

  it("일정 초안 카드 · 알레르기 목록에는 화면 안의 움직임을 걸지 않는다", () => {
    // 알레르기 목록은 감싸는 구역(02 · 11 의 `SafetySection`)까지 본다 — 바깥에서 `Swap` 으로 감싸도 같은 일이다.
    for (const path of [
      "components/event-draft-card.tsx",
      "components/health-safety-list.tsx",
      "components/safety-section.tsx",
    ]) {
      expect(sources.get(path), path).not.toMatch(
        /\b(useFlip|useReveal|useEnterMotion|useMotionCue|playEnter|Swap)\b/,
      );
    }
  });
});

describe("움직임은 `lib/motion.ts` 를 거친다", () => {
  it("`Element.animate` 를 직접 부르는 곳은 `lib/motion.ts` 하나다", () => {
    // 🚨 직접 부르면 첫 렌더를 건너뛰는 것 · reduced-motion · 토큰 읽기를 화면마다 다시 짜야 한다.
    expect(filesMatching(/\.animate\(/)).toEqual(["lib/motion.ts"]);
  });

  it("`lib/motion.ts` 는 시간 · 거리를 숫자로 적지 않고 토큰을 읽는다", () => {
    const code = sources.get("lib/motion.ts") ?? "";
    // 🚨 #231 리뷰 — 규칙마다 숫자가 흩어져 표에 없는 값이 생겼다. `globals.css` 의 변수가 원본이다.
    expect(code).not.toMatch(/duration:\s*\d/);
    expect(code).not.toMatch(/\d+px/);
    expect(code).toMatch(/--motion-\$\{name\}/);
  });
});
