/**
 * 비교기(`compare.ts`)가 규칙대로 잡는지 (#559). 서버 계약 없이 돈다.
 *
 * 🚨 **"다 통과" 가 "아무것도 안 봤다" 가 아닌지를 여기서 건다.** 칸을 못 읽는 비교기는 늘 빈 목록을
 *    돌려줘서 `openapi.test.ts` 가 늘 통과한다 (서버 인증 경계 테스트가 빈 라우트 목록을 보고 늘
 *    통과했던 #179 와 같은 자리).
 */

import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

import {
  compareSchema,
  directionsOf,
  readWebTypes,
  type JsonSchema,
  type OpenApiSpec,
} from "./compare";

const web = readWebTypes(
  [resolve(__dirname, "fixtures/web-types.ts")],
  resolve(__dirname, "../tsconfig.json"),
);

const ColorEnum: JsonSchema = { type: "string", enum: ["red", "blue"] };
const spec: OpenApiSpec = { paths: {}, components: { schemas: { Color: ColorEnum } } };

const response = new Set(["response"] as const);
const request = new Set(["request"] as const);

const ItemOut = (over: Partial<Record<string, JsonSchema>> = {}): JsonSchema => ({
  type: "object",
  properties: {
    id: { type: "string" },
    note: { anyOf: [{ type: "string" }, { type: "null" }] },
    color: { $ref: "#/components/schemas/Color" },
    label: { type: "string" },
    extra: { type: "string" },
    ...over,
  } as Record<string, JsonSchema>,
  required: ["id", "note", "color", "label"],
});

describe("응답 — 서버가 보내는 것을 웹이 다 받는가", () => {
  it("맞는 모양은 아무것도 내지 않는다", () => {
    expect(compareSchema(ItemOut(), web("Item"), response, spec)).toEqual([]);
  });

  it("웹이 기다리는 칸이 서버에 없으면 잡는다", () => {
    const schema = ItemOut();
    delete schema.properties!.label;
    expect(compareSchema(schema, web("Item"), response, spec)).toEqual([
      "label: 웹이 기다리는데 서버 응답에 없는 칸",
    ]);
  });

  it("서버에만 있는 칸은 괜찮다 — 웹이 안 읽을 뿐이다", () => {
    const schema = ItemOut({ added: { type: "string" } });
    expect(compareSchema(schema, web("Item"), response, spec)).toEqual([]);
  });

  it("서버가 null 을 보낼 수 있는데 웹이 모르면 잡는다", () => {
    const schema = ItemOut({ label: { anyOf: [{ type: "string" }, { type: "null" }] } });
    expect(compareSchema(schema, web("Item"), response, spec)).toEqual([
      "label: 서버가 보낼 수 있는데 웹 타입이 못 받음 — null",
    ]);
  });

  it("값의 종류가 다르면 잡는다 (Decimal 이 문자열로 오는 자리)", () => {
    const schema = ItemOut({ id: { type: "integer" } });
    expect(compareSchema(schema, web("Item"), response, spec)).toEqual([
      "id: 서버가 보낼 수 있는데 웹 타입이 못 받음 — number",
    ]);
  });

  it("enum 에 서버 값이 늘면 잡는다", () => {
    const widened: OpenApiSpec = {
      paths: {},
      components: { schemas: { Color: { type: "string", enum: ["red", "blue", "green"] } } },
    };
    expect(compareSchema(ItemOut(), web("Item"), response, widened)).toEqual([
      'color: 서버가 보낼 수 있는데 웹 타입이 못 받음 — "green"',
    ]);
    expect(
      compareSchema(widened.components.schemas.Color!, web("Color"), response, widened),
    ).toEqual(['(값) 서버가 보내는데 웹 타입에 없음: "green"']);
  });

  it("서버가 그냥 문자열로 둔 칸을 웹이 좁힌 것은 잡지 않는다", () => {
    const schema = ItemOut({ color: { type: "string" } });
    expect(compareSchema(schema, web("Item"), response, spec)).toEqual([]);
  });

  it("allow 에 적은 칸은 건너뛴다", () => {
    const schema = ItemOut();
    delete schema.properties!.label;
    expect(compareSchema(schema, web("Item"), response, spec, new Set(["label"]))).toEqual([]);
  });
});

describe("요청 — 웹이 보내는 것을 서버가 다 받는가", () => {
  const SendIn = (over: Partial<JsonSchema> = {}): JsonSchema => ({
    type: "object",
    properties: {
      title: { type: "string" },
      memo: { type: "string" },
      starts_at: { anyOf: [{ type: "string" }, { type: "null" }] },
      colors: { type: "array", items: { $ref: "#/components/schemas/Color" } },
    },
    required: ["title", "colors"],
    ...over,
  });

  it("맞는 모양은 아무것도 내지 않는다", () => {
    expect(compareSchema(SendIn(), web("SendBody"), request, spec)).toEqual([]);
  });

  it("웹이 보내는 칸을 서버가 모르면 잡는다", () => {
    const schema = SendIn();
    delete schema.properties!.memo;
    expect(compareSchema(schema, web("SendBody"), request, spec)).toEqual([
      "memo: 웹이 보내는데 서버가 모르는 칸",
    ]);
  });

  it("서버 필수 칸을 웹이 빠뜨릴 수 있으면 잡는다", () => {
    const schema = SendIn({ required: ["title", "colors", "memo"] });
    expect(compareSchema(schema, web("SendBody"), request, spec)).toEqual([
      "memo: 서버 필수 칸인데 웹이 빠뜨릴 수 있음 (`?`)",
    ]);
  });

  it("서버에 새 필수 칸이 생기면 잡고, 선택 칸이면 넘어간다", () => {
    const required = SendIn();
    required.properties!.added = { type: "string" };
    required.required = ["title", "colors", "added"];
    expect(compareSchema(required, web("SendBody"), request, spec)).toEqual([
      "added: 서버 필수 칸인데 웹 타입에 없음",
    ]);
    required.required = ["title", "colors"];
    expect(compareSchema(required, web("SendBody"), request, spec)).toEqual([]);
  });

  it("웹이 null 을 보낼 수 있는데 서버가 안 받으면 잡는다", () => {
    const schema = SendIn();
    schema.properties!.starts_at = { type: "string" };
    expect(compareSchema(schema, web("SendBody"), request, spec)).toEqual([
      "starts_at: 웹이 보낼 수 있는데 서버가 안 받음 — null",
    ]);
  });

  it("배열 원소의 enum 도 맞춘다", () => {
    const schema = SendIn();
    schema.properties!.colors = { type: "array", items: { type: "string", enum: ["red"] } };
    expect(compareSchema(schema, web("SendBody"), request, spec)).toEqual([
      'colors: 웹이 보낼 수 있는데 서버가 안 받음 — 원소 "blue"',
    ]);
  });

  it("enum 에 서버 값이 느는 것은 괜찮다 — 웹이 안 보낼 뿐이다", () => {
    const widened: JsonSchema = { type: "string", enum: ["red", "blue", "green"] };
    expect(compareSchema(widened, web("Color"), request, spec)).toEqual([]);
    const narrowed: JsonSchema = { type: "string", enum: ["red"] };
    expect(compareSchema(narrowed, web("Color"), request, spec)).toEqual([
      '(값) 웹이 보내는데 서버가 안 받음: "blue"',
    ]);
  });
});

describe("객체 유니온", () => {
  it("한 갈래에만 있는 칸도 비교한다 — 어느 갈래에 없으면 빠질 수 있는 칸이다", () => {
    const fields = web("Mixed").fields!;
    expect([...fields.keys()].sort()).toEqual(["kind", "only_a", "only_b"]);
    expect(fields.get("only_a")!.optional).toBe(true);
    expect(fields.get("kind")!.optional).toBe(false);

    const schema: JsonSchema = {
      type: "object",
      properties: {
        kind: { type: "string", enum: ["a", "b", "c"] },
        only_a: { type: "string" },
      },
      required: ["kind"],
    };
    expect(compareSchema(schema, web("Mixed"), response, spec)).toEqual([
      "only_b: 웹이 기다리는데 서버 응답에 없는 칸",
      'kind: 서버가 보낼 수 있는데 웹 타입이 못 받음 — "c"',
    ]);
  });
});

describe("웹 타입 경로", () => {
  it("칸 · 배열 원소 · 유니온 갈래를 따라간다", () => {
    expect([...web("Box.items[]").fields!.keys()]).toEqual([
      "id",
      "note",
      "color",
      "label",
      "extra",
    ]);
    expect([...web("Box.owner").fields!.keys()]).toEqual(["name"]);
    expect([...web("Reply<type=done>").fields!.keys()]).toEqual(["type", "completed"]);
  });

  it("없는 타입 · 칸 · 갈래는 던진다 — 조용히 빈 타입으로 비교하지 않는다", () => {
    expect(() => web("Nope")).toThrow("웹 타입 Nope 이 없어요");
    expect(() => web("Box.nope")).toThrow("nope 칸이 없어요");
    expect(() => web("Reply<type=gone>")).toThrow("type=gone 인 갈래가 없어요");
  });
});

describe("스키마가 쓰이는 방향", () => {
  it("요청 본문과 응답에서 가리킨 스키마를 안쪽까지 모은다", () => {
    const paths: OpenApiSpec = {
      paths: {
        "/boxes": {
          post: {
            requestBody: {
              content: { "application/json": { schema: { $ref: "#/components/schemas/SendIn" } } },
            },
            responses: {
              "200": {
                content: {
                  "application/json": { schema: { $ref: "#/components/schemas/BoxOut" } },
                },
              },
            },
          },
        },
      },
      components: {
        schemas: {
          SendIn: { type: "object", properties: { color: { $ref: "#/components/schemas/Color" } } },
          BoxOut: {
            type: "object",
            properties: { items: { type: "array", items: { $ref: "#/components/schemas/Color" } } },
          },
          Color: ColorEnum,
        },
      },
    };
    const dirs = directionsOf(paths);
    expect([...dirs.get("SendIn")!]).toEqual(["request"]);
    expect([...dirs.get("BoxOut")!]).toEqual(["response"]);
    expect([...dirs.get("Color")!].sort()).toEqual(["request", "response"]);
  });
});
