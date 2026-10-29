import { describe, expect, it } from "vitest";

import { readPushEvent } from "@/lib/native/push";

/** 셸이 쏘는 `icatch:push` 를 읽는다 (#609 FE-4). 이 이벤트 이름은 페이지 안의 누구든 쏠 수 있어서 모양을 다시 본다. */
describe("셸 알림 이벤트", () => {
  it("토큰과 누른 화면 주소만 받는다", () => {
    expect(readPushEvent({ type: "token", token: "t" })).toEqual({ type: "token", token: "t" });
    expect(readPushEvent({ type: "open", path: "/child/c1/calendar?date=2026-11-02" })).toEqual({
      type: "open",
      path: "/child/c1/calendar?date=2026-11-02",
    });
  });

  it("🚨 알림으로 열어도 되는 모양이 아닌 주소는 버린다 — 그대로 router.push 로 가는 값이다", () => {
    for (const path of [
      "https://evil.example/",
      "//evil.example",
      "/child/c1/memories",
      "/child/../settings/withdraw",
      "/child/c1/calendar?date=x",
    ]) {
      expect(readPushEvent({ type: "open", path })).toBeNull();
    }
  });

  it("모양이 다르면 버린다", () => {
    for (const detail of [
      null,
      "token",
      { type: "token", token: "" },
      { type: "token" },
      { type: "x" },
    ]) {
      expect(readPushEvent(detail)).toBeNull();
    }
  });
});
