import { describe, expect, it } from "vitest";

import { scarcityCopy } from "./scarcity-copy";

const all = (copy: ReturnType<typeof scarcityCopy>) => `${copy.title} ${copy.body}`;
const record = (label: string, count: number) => ({ label, count });

describe("scarcityCopy — Agent별 기록 수와 이번 추천의 근거를 섞어 말하지 않는다 (#662)", () => {
  it("Agent 하나의 일반 추천만 서면 그 Agent 기록 수를 말한다", () => {
    const copy = scarcityCopy({
      records: [record("식사", 22)],
      fallbackCount: 999,
      hasPersonal: false,
    });

    expect(copy.title).toBe("이번에는 일반 추천으로 골랐어요");
    expect(copy.body).toBe(
      "식사 기록은 22건 있어요. 이번 추천에 직접 사용한 아이 기록이 없어 추천 아래 적힌 기준으로 골랐어요.",
    );
    expect(all(copy)).not.toContain("999");
  });

  it("개인화와 일반이 섞이면 일반 추천이라고 적힌 것만 가리킨다", () => {
    const copy = scarcityCopy({
      records: [record("식사", 22)],
      fallbackCount: 22,
      hasPersonal: true,
    });

    expect(copy.title).toBe("일부는 일반 추천으로 골랐어요");
    expect(copy.body).toBe(
      "식사 기록은 22건 있지만, 일반 추천이라고 적힌 것에는 직접 연결한 아이 기록이 없어요.",
    );
    expect(copy.body).not.toContain("위 추천");
  });

  it.each([
    {
      records: [record("식사", 22), record("놀이", 8)],
      summary: "영역별 기록은 식사 22건 · 놀이 8건이에요.",
    },
    {
      records: [record("식사", 22), record("놀이", 0)],
      summary: "식사 기록은 22건 있고, 놀이 기록은 아직 없어요.",
    },
    {
      records: [record("식사", 0), record("놀이", 8)],
      summary: "놀이 기록은 8건 있고, 식사 기록은 아직 없어요.",
    },
    {
      records: [record("식사", 0), record("놀이", 0)],
      summary: "식사와 놀이 기록은 아직 없어요.",
    },
  ])("Agent 둘의 기록을 합치지 않고 보여 준다: $summary", ({ records, summary }) => {
    const copy = scarcityCopy({ records, fallbackCount: 30, hasPersonal: false });

    expect(copy.body).toContain(summary);
    expect(copy.body).not.toContain("30건");
  });

  it("Agent 하나의 0건도 숫자로 뭉개지 않고 아직 없다고 말한다", () => {
    const copy = scarcityCopy({
      records: [record("놀이", 0)],
      fallbackCount: 0,
      hasPersonal: false,
    });

    expect(copy.body).toContain("놀이 기록은 아직 없어요.");
    expect(copy.body).not.toContain("0건");
  });

  it("일반 추천이 없으면 합계는 방어 문구에만 쓴다", () => {
    const some = scarcityCopy({ records: [], fallbackCount: 3, hasPersonal: false });
    const none = scarcityCopy({ records: [], fallbackCount: 0, hasPersonal: false });

    expect(some.body).toBe("쌓인 기록은 3건 있어요. 근거 없이 추천을 만들지는 않을게요.");
    expect(none.body).toBe("쌓인 기록이 아직 없어요. 근거 없이 추천을 만들지는 않을게요.");
  });

  it("질문이 있을 때만 다음 추천에 참고한다는 문장을 붙인다", () => {
    const asked = scarcityCopy({
      records: [record("식사", 22)],
      fallbackCount: 22,
      hasPersonal: false,
      asked: true,
    });
    const quiet = scarcityCopy({
      records: [record("식사", 22)],
      fallbackCount: 22,
      hasPersonal: false,
    });

    expect(asked.body).toContain("하나만 알려주시면 다음 추천에 참고할게요.");
    expect(quiet.body).not.toContain("하나만 알려주시면");
  });

  it("일반 추천이 없으면 질문이 있어도 후속 문장을 붙이지 않는다", () => {
    const copy = scarcityCopy({
      records: [],
      fallbackCount: 22,
      hasPersonal: false,
      asked: true,
    });

    expect(copy.body).not.toContain("하나만 알려주시면");
  });

  it.each([
    { records: [record("식사", 22)], fallbackCount: 22, hasPersonal: false },
    { records: [record("놀이", 0)], fallbackCount: 0, hasPersonal: false },
    {
      records: [record("식사", 22), record("놀이", 8)],
      fallbackCount: 30,
      hasPersonal: false,
    },
    { records: [record("식사", 22)], fallbackCount: 22, hasPersonal: true },
  ])("기록이 적다고 단정하거나 다음 결과를 약속하지 않는다 %#", (state) => {
    const text = all(scarcityCopy(state));

    for (const banned of ["뿐이에요", "더 쌓이면", "부족해요", "다음엔", "다음부터", "이 주제로"]) {
      expect(text).not.toContain(banned);
    }
  });
});
