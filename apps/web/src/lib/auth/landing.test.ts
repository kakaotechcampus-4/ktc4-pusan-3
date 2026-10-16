import { describe, expect, it } from "vitest";

import type { Me } from "@/lib/api/types";
import { landingPath } from "@/lib/auth/landing";

/** 로그인한 보호자가 처음 설 화면 — 앱을 다시 켰을 때와 로그인 직후가 같은 함수다 (#465). */

function meWith(...childIds: string[]): Me {
  return {
    id: "p1",
    nickname: null,
    children: childIds.map((child_id) => ({
      child_id,
      nickname: "별명",
      age_display: "3살",
      relation: "mother",
      role: "owner",
      consent_required: [],
    })),
  };
}

describe("landingPath", () => {
  it("마지막에 본 아이가 내 목록에 있으면 그 아이의 홈", () => {
    expect(landingPath(meWith("c1", "c2"), "c2")).toBe("/child/c2/home");
  });

  it("🚨 기억해 둔 아이가 목록에 없으면 그 id 를 쓰지 않고 첫 아이로 — 다른 계정이 남긴 값일 수 있다", () => {
    expect(landingPath(meWith("c1"), "someone-else")).toBe("/child/c1/home");
  });

  it("기억해 둔 아이가 없으면 첫 아이", () => {
    expect(landingPath(meWith("c1", "c2"), null)).toBe("/child/c1/home");
  });

  it("🚨 아이가 없으면 01 이 아니라 00-1 이다 (#96)", () => {
    expect(landingPath(meWith(), null)).toBe("/start");
  });
});
