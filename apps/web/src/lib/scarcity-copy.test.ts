import { describe, expect, it } from "vitest";

import { scarcityCopy } from "./scarcity-copy";

const all = (copy: ReturnType<typeof scarcityCopy>) => `${copy.title} ${copy.body}`;

describe("scarcityCopy — 누적 기록 건수와 이번 추천의 근거를 섞어 말하지 않는다 (#662)", () => {
  it("일반 추천만 섰고 기록이 있으면, 건수는 그대로 말하고 이번에 쓴 아이 기록이 없었다고 한다", () => {
    const copy = scarcityCopy({ count: 22, hasGeneral: true, hasPersonal: false });

    expect(copy.title).toBe("이번에는 일반 기준으로 골랐어요");
    expect(copy.body).toContain("이 주제로 기록은 22건 있어요.");
    expect(copy.body).toContain("이번 추천에 직접 사용한 아이 기록이 없어 일반 기준으로 골랐어요.");
  });

  it("개인화와 일반이 섞였으면 또래 기준이라고 적힌 추천만 가리킨다", () => {
    const copy = scarcityCopy({ count: 22, hasGeneral: true, hasPersonal: true });

    expect(copy.title).toBe("일부는 일반 기준으로 골랐어요");
    expect(copy.body).toContain("이 주제로 기록은 22건 있지만,");
    expect(copy.body).toContain("또래 기준이라고 적힌 추천에는 직접 연결한 아이 기록이 없어요.");
    // 🚨 위에 선 개인화 추천까지 일반이라고 부르지 않는다 (#512 리뷰)
    expect(copy.body).not.toContain("위 추천");
  });

  it("기록이 0건이면 쌓인 기록이 아직 없다고 말한다", () => {
    const copy = scarcityCopy({ count: 0, hasGeneral: true, hasPersonal: false });

    expect(copy.title).toBe("이번에는 일반 기준으로 골랐어요");
    expect(copy.body).toContain("이 주제로 쌓인 기록이 아직 없어요.");
    expect(copy.body).toContain("그래서 이번에는 일반 기준으로 골랐어요.");
    expect(copy.body).not.toContain("0건");
  });

  it("일반 추천도 서지 않았으면 근거 없이 추천을 만들지 않는다고만 말한다", () => {
    const some = scarcityCopy({ count: 3, hasGeneral: false, hasPersonal: false });
    const none = scarcityCopy({ count: 0, hasGeneral: false, hasPersonal: false });

    expect(some.body).toContain("이 주제로 기록은 3건 있어요.");
    expect(none.body).toContain("이 주제로 쌓인 기록이 아직 없어요.");
    for (const copy of [some, none])
      expect(copy.body).toContain("근거 없이 추천을 만들지는 않을게요.");
  });

  it.each([
    { count: 0, hasGeneral: true, hasPersonal: false },
    { count: 1, hasGeneral: true, hasPersonal: false },
    { count: 22, hasGeneral: true, hasPersonal: false },
    { count: 22, hasGeneral: true, hasPersonal: true },
    { count: 22, hasGeneral: false, hasPersonal: false },
  ])("기록이 적다고 단정하거나 쌓이면 달라진다고 약속하지 않는다 %j", (state) => {
    const text = all(scarcityCopy(state));

    for (const banned of ["뿐이에요", "더 쌓이면", "부족해요", "다음엔", "다음부터"]) {
      expect(text).not.toContain(banned);
    }
  });

  it("질문이 있을 때만 질문 하나를 이어 붙인다", () => {
    const asked = scarcityCopy({ count: 22, hasGeneral: true, hasPersonal: false, asked: true });
    const quiet = scarcityCopy({ count: 22, hasGeneral: true, hasPersonal: false, asked: false });

    expect(asked.body).toContain("하나만 알려주시면 다음부터 달라져요.");
    expect(quiet.body).not.toContain("하나만 알려주시면");
  });

  it("일반 추천이 없으면 질문이 있어도 약속을 붙이지 않는다", () => {
    const copy = scarcityCopy({ count: 22, hasGeneral: false, hasPersonal: false, asked: true });

    expect(copy.body).not.toContain("하나만 알려주시면");
  });
});
