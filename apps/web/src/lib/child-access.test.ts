import { describe, expect, it } from "vitest";

import type { Me } from "@/lib/api/types";

import { judgeChildAccess } from "./child-access";

const me: Me = {
  id: "p1",
  nickname: "보호자",
  children: [
    {
      child_id: "c1",
      nickname: "아이",
      age_display: "만 4세",
      relation: "mother",
      role: "owner",
      consent_required: [],
    },
  ],
};

const base = { me, failed: false, fetchedAfterEnter: true };

describe("judgeChildAccess", () => {
  it("목록에 있으면 본다", () => {
    expect(judgeChildAccess({ ...base, childId: "c1" })).toBe("allowed");
  });

  it("들어온 뒤 받은 목록에도 없으면 막는다", () => {
    expect(judgeChildAccess({ ...base, childId: "c2" })).toBe("denied");
  });

  it("🚨 UUID 모양이 아닌 주소도 목록에 없는 것으로 막는다", () => {
    expect(judgeChildAccess({ ...base, childId: "not-a-uuid" })).toBe("denied");
  });

  it("🚨 들어오기 전에 받아 둔 목록에 없으면 막지 않고 새로 받는다 — 막 만든 아이일 수 있다", () => {
    expect(judgeChildAccess({ ...base, childId: "c2", fetchedAfterEnter: false })).toBe("stale");
  });

  it("목록을 아직 못 받았으면 기다린다", () => {
    expect(
      judgeChildAccess({ childId: "c1", me: undefined, failed: false, fetchedAfterEnter: false }),
    ).toBe("checking");
  });

  it("🚨 목록을 받지 못했으면 막지 않는다 — 볼 수 없다는 뜻이 아니다", () => {
    expect(
      judgeChildAccess({ childId: "c1", me: undefined, failed: true, fetchedAfterEnter: true }),
    ).toBe("unknown");
    expect(judgeChildAccess({ ...base, childId: "c2", failed: true })).toBe("unknown");
  });

  it("앞서 확인된 아이는 다시 받다 실패해도 그대로 본다", () => {
    expect(judgeChildAccess({ ...base, childId: "c1", failed: true })).toBe("allowed");
  });

  it("아이가 0명인 계정은 어느 아이도 막는다", () => {
    expect(judgeChildAccess({ ...base, me: { ...me, children: [] }, childId: "c1" })).toBe(
      "denied",
    );
  });
});
