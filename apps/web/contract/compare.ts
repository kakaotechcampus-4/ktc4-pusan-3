/**
 * 웹이 손으로 적은 API 타입(`src/lib/api/types.ts`)과 서버 OpenAPI 를 칸 단위로 맞춘다 (#559).
 *
 * 무엇을 보는가 — 칸이 있는가, 빠질 수 있는가(`?`), 어떤 값이 오가는가(문자열 · 숫자 · `null` …),
 * 문자열이면 어떤 값만 오가는가(enum · 리터럴 유니온). 칸 안쪽의 객체는 그 객체의 스키마를 따로 대응해서 본다.
 *
 * 🚨 **방향마다 규칙이 다르다.** 받는 쪽이 보내는 쪽보다 넓으면 괜찮고, 좁으면 어긋남이다.
 *
 * | | 응답 (서버 → 웹) | 요청 (웹 → 서버) |
 * | --- | --- | --- |
 * | 한쪽에만 있는 칸 | 웹에만 있으면 어긋남 (웹이 기다리는 칸이 안 온다). 서버에만 있으면 괜찮다 | 웹에만 있으면 어긋남 (서버가 모르는 칸 · `extra="forbid"` 면 400). 서버에만 있으면 선택 칸일 때만 괜찮다 |
 * | 빠질 수 있는가 | 보지 않는다 (아래) | 서버가 필수인데 웹이 빠뜨릴 수 있으면 어긋남 |
 * | 값의 종류 · 리터럴 | 서버가 보낼 수 있는 것을 웹이 다 받아야 한다 | 웹이 보낼 수 있는 것을 서버가 다 받아야 한다 |
 *
 * 양쪽에 다 쓰이는 스키마는 두 규칙을 다 건다.
 *
 * ⚠️ **응답의 `required` 는 믿지 않는다.** Pydantic 은 기본값이 있는 칸을 OpenAPI 에서 필수로 적지 않지만
 *    응답에는 늘 싣는다. 그 칸마다 "빠질 수 있다" 고 잡으면 잡음만 남는다. 칸을 빼고 보내는 곳
 *    (`exclude_none` — 오류 봉투 · `GET /auth/{provider}/status`)은 `null` 검사에 걸리고, 대응표의 `allow` 로 둔다.
 * ⚠️ **리터럴은 양쪽이 다 값을 정했을 때만 맞춘다.** 서버가 그냥 `str` 로 둔 칸을 웹이 `"food" | …` 로
 *    좁힌 것은 웹이 도메인을 적어 둔 것이지 어긋남이 아니다 — 서버가 값을 약속하지 않았으니 알 수 없다.
 */

import ts from "typescript";

/* ── OpenAPI 쪽 ──────────────────────────────────────────────────────── */

export interface JsonSchema {
  $ref?: string;
  type?: string | string[];
  enum?: unknown[];
  const?: unknown;
  anyOf?: JsonSchema[];
  oneOf?: JsonSchema[];
  allOf?: JsonSchema[];
  items?: JsonSchema;
  properties?: Record<string, JsonSchema>;
  required?: string[];
  additionalProperties?: boolean | JsonSchema;
}

export interface OpenApiSpec {
  paths: Record<string, Record<string, OpenApiOperation>>;
  components: { schemas: Record<string, JsonSchema> };
}

interface OpenApiOperation {
  requestBody?: { content?: Record<string, { schema?: JsonSchema }> };
  responses?: Record<string, { content?: Record<string, { schema?: JsonSchema }> }>;
}

export type Direction = "request" | "response";

const refName = (ref: string) => ref.slice(ref.lastIndexOf("/") + 1);

/** 스키마 안에서 `$ref` 로 가리키는 컴포넌트 이름을 전부 모은다 (안쪽 객체까지). */
function collectRefs(schema: JsonSchema | undefined, out: Set<string>, all: OpenApiSpec) {
  if (!schema) return;
  if (schema.$ref) {
    const name = refName(schema.$ref);
    if (out.has(name)) return;
    out.add(name);
    collectRefs(all.components.schemas[name], out, all);
    return;
  }
  for (const sub of [...(schema.anyOf ?? []), ...(schema.oneOf ?? []), ...(schema.allOf ?? [])]) {
    collectRefs(sub, out, all);
  }
  collectRefs(schema.items, out, all);
  for (const p of Object.values(schema.properties ?? {})) collectRefs(p, out, all);
  if (typeof schema.additionalProperties === "object") {
    collectRefs(schema.additionalProperties, out, all);
  }
}

/** 컴포넌트마다 요청 본문에 쓰이는지, 응답에 쓰이는지. 둘 다일 수 있다. */
export function directionsOf(spec: OpenApiSpec): Map<string, Set<Direction>> {
  const used = new Map<string, Set<Direction>>();
  const mark = (schema: JsonSchema | undefined, dir: Direction) => {
    const names = new Set<string>();
    collectRefs(schema, names, spec);
    for (const n of names) {
      if (!used.has(n)) used.set(n, new Set());
      used.get(n)!.add(dir);
    }
  };
  for (const ops of Object.values(spec.paths)) {
    for (const op of Object.values(ops)) {
      for (const c of Object.values(op.requestBody?.content ?? {})) mark(c.schema, "request");
      for (const r of Object.values(op.responses ?? {})) {
        for (const c of Object.values(r.content ?? {})) mark(c.schema, "response");
      }
    }
  }
  return used;
}

/** 값의 종류. `any` 는 무엇이든 — 비교하지 않는다. */
export type Kind = "string" | "number" | "boolean" | "null" | "array" | "object";

export interface ValueShape {
  any: boolean;
  kinds: Set<Kind>;
  /** 문자열이 이 값들로만 제한될 때. `null` 이면 아무 문자열이나. */
  literals: Set<string> | null;
  /** 배열이면 원소의 모양. `null` 이면 원소를 모른다 (비교하지 않는다). */
  items: ValueShape | null;
}

const emptyShape = (): ValueShape => ({
  any: false,
  kinds: new Set(),
  literals: null,
  items: null,
});

/** 모양 여럿을 "이 중 무엇이든" 으로 합친다. 유니온 · 배열 원소에 쓴다. */
function mergeShapes(shapes: ValueShape[]): ValueShape {
  const out = emptyShape();
  let freeString = false;
  const literals = new Set<string>();
  const items: ValueShape[] = [];
  for (const s of shapes) {
    out.any ||= s.any;
    s.kinds.forEach((k) => out.kinds.add(k));
    if (s.kinds.has("string")) {
      if (s.literals) s.literals.forEach((v) => literals.add(v));
      else freeString = true;
    }
    if (s.items) items.push(s.items);
  }
  if (out.kinds.has("string") && !freeString) out.literals = literals;
  if (items.length) out.items = mergeShapes(items);
  return out;
}

function shapeOfSchema(schema: JsonSchema, spec: OpenApiSpec): ValueShape {
  if (schema.$ref) {
    const target = spec.components.schemas[refName(schema.$ref)];
    if (target?.enum) return shapeOfSchema(target, spec);
    return { ...emptyShape(), kinds: new Set(["object"]) };
  }
  const subs = [...(schema.anyOf ?? []), ...(schema.oneOf ?? []), ...(schema.allOf ?? [])];
  if (subs.length) return mergeShapes(subs.map((sub) => shapeOfSchema(sub, spec)));

  const values = schema.const !== undefined ? [schema.const] : schema.enum;
  if (values) {
    return mergeShapes(
      values.map((v) => {
        const shape = emptyShape();
        if (v === null) shape.kinds.add("null");
        else if (typeof v === "string") {
          shape.kinds.add("string");
          shape.literals = new Set([v]);
        } else if (typeof v === "number") shape.kinds.add("number");
        else if (typeof v === "boolean") shape.kinds.add("boolean");
        return shape;
      }),
    );
  }

  const types = Array.isArray(schema.type) ? schema.type : schema.type ? [schema.type] : [];
  if (!types.length) return { ...emptyShape(), any: true };
  const shape = emptyShape();
  for (const t of types) shape.kinds.add(t === "integer" ? "number" : (t as Kind));
  if (shape.kinds.has("array") && schema.items) shape.items = shapeOfSchema(schema.items, spec);
  return shape;
}

/* ── TypeScript 쪽 ───────────────────────────────────────────────────── */

export interface WebField {
  optional: boolean;
  shape: ValueShape;
}

export interface WebType {
  /** 객체 타입이면 칸. 문자열 유니온(enum 짝)이면 `null`. */
  fields: Map<string, WebField> | null;
  shape: ValueShape;
}

function shapeOfType(type: ts.Type, checker: ts.TypeChecker): ValueShape {
  const F = ts.TypeFlags;
  const shape = emptyShape();
  if (type.flags & (F.Any | F.Unknown)) shape.any = true;
  else if (type.isUnion()) return mergeShapes(type.types.map((m) => shapeOfType(m, checker)));
  else if (type.flags & F.Undefined || type.flags & F.Void) return shape;
  else if (type.flags & F.Null) shape.kinds.add("null");
  else if (type.isStringLiteral()) {
    shape.kinds.add("string");
    shape.literals = new Set([type.value]);
  } else if (type.flags & F.StringLike) shape.kinds.add("string");
  else if (type.flags & F.NumberLike) shape.kinds.add("number");
  else if (type.flags & F.BooleanLike) shape.kinds.add("boolean");
  else if (type.isIntersection()) {
    // `string & { __brand }` 같은 표식 타입 — 원시 쪽이 값의 종류다.
    const primitive = type.types.find((m) => !(m.flags & F.Object));
    if (primitive) return shapeOfType(primitive, checker);
    shape.kinds.add("object");
  } else if (checker.isArrayType(type) || checker.isTupleType(type)) {
    shape.kinds.add("array");
    const element = checker.getIndexTypeOfType(type, ts.IndexKind.Number);
    if (element) shape.items = shapeOfType(element, checker);
  } else if (type.flags & F.Object) shape.kinds.add("object");
  else shape.any = true;
  return shape;
}

/**
 * 웹 타입을 이름 또는 **칸 경로**로 찾는다.
 *
 * 서버가 따로 이름 붙인 안쪽 객체를 웹은 칸 안에 바로 적어 둔 경우가 많아서(`Me` 의 `children` 등),
 * 이름만으로는 짝을 못 찾는다. 경로는 내보낸 타입 이름에서 시작한다.
 *
 * - `.칸` — 그 칸의 타입 (`null` · `undefined` 는 벗긴다)
 * - `[]` — 배열의 원소
 * - `<칸=값>` — 유니온에서 그 칸이 그 문자열인 갈래 (`ChatReply<type=note>`)
 */
export type WebResolver = (path: string) => WebType;

export function readWebTypes(files: string[], tsconfigPath: string): WebResolver {
  const config = ts.readConfigFile(tsconfigPath, ts.sys.readFile);
  const parsed = ts.parseJsonConfigFileContent(
    config.config,
    ts.sys,
    tsconfigPath.slice(0, tsconfigPath.lastIndexOf("/")),
  );
  const program = ts.createProgram(files, { ...parsed.options, noEmit: true });
  const checker = program.getTypeChecker();

  const roots = new Map<string, ts.Type>();
  for (const file of files) {
    const source = program.getSourceFile(file);
    const moduleSymbol = source && checker.getSymbolAtLocation(source);
    if (!moduleSymbol) throw new Error(`${file} 를 모듈로 읽지 못했어요`);
    for (const exported of checker.getExportsOfModule(moduleSymbol)) {
      const symbol =
        exported.flags & ts.SymbolFlags.Alias ? checker.getAliasedSymbol(exported) : exported;
      if (!(symbol.flags & (ts.SymbolFlags.Interface | ts.SymbolFlags.TypeAlias))) continue;
      roots.set(exported.name, checker.getDeclaredTypeOfSymbol(symbol));
    }
  }

  const step = (type: ts.Type, token: string, path: string): ts.Type => {
    const bare = checker.getNonNullableType(type);
    if (token === "[]") {
      const element = checker.getIndexTypeOfType(bare, ts.IndexKind.Number);
      if (!element) throw new Error(`${path}: 배열이 아니에요`);
      return element;
    }
    const pick = /^<(\w+)=(\w+)>$/.exec(token);
    if (pick) {
      const [, key, value] = pick;
      const members = bare.isUnion() ? bare.types : [bare];
      const member = members.find((m) => {
        const prop = checker.getPropertyOfType(m, key!);
        const t = prop && checker.getTypeOfSymbol(prop);
        return !!t && t.isStringLiteral() && t.value === value;
      });
      if (!member) throw new Error(`${path}: ${key}=${value} 인 갈래가 없어요`);
      return member;
    }
    const prop = checker.getPropertyOfType(bare, token.slice(1));
    if (!prop) throw new Error(`${path}: ${token.slice(1)} 칸이 없어요`);
    return checker.getTypeOfSymbol(prop);
  };

  return (path) => {
    const [, name, rest] = /^(\w+)(.*)$/.exec(path) ?? [];
    const root = name && roots.get(name);
    if (!root) throw new Error(`${path}: 웹 타입 ${name} 이 없어요`);
    const tokens = rest!.match(/\.\w+|\[\]|<\w+=\w+>/g) ?? [];
    if (tokens.join("") !== rest) throw new Error(`${path}: 경로를 읽지 못했어요`);
    let type = tokens.reduce((t, token) => step(t, token, path), root);
    type = checker.getNonNullableType(type);

    const shape = shapeOfType(type, checker);
    const isObject = !shape.any && shape.kinds.size === 1 && shape.kinds.has("object");
    if (!isObject) return { fields: null, shape };

    // 🚨 객체 유니온은 갈래 전부의 칸을 합쳐서 본다. `getPropertiesOfType` 은 공통 칸만 돌려줘서,
    //    한 갈래에만 있는 칸이 비교에서 조용히 빠진다. 어느 갈래에 없는 칸은 빠질 수 있는 칸이다.
    const members = type.isUnion() ? type.types : [type];
    const perMember = members.map(
      (m) => new Map(checker.getPropertiesOfType(m).map((p) => [p.name, p])),
    );
    const names = new Set(perMember.flatMap((props) => [...props.keys()]));
    const fields = new Map<string, WebField>();
    for (const name of names) {
      let optional = false;
      const shapes: ValueShape[] = [];
      for (const props of perMember) {
        const prop = props.get(name);
        if (!prop) {
          optional = true;
          continue;
        }
        const propType = checker.getTypeOfSymbol(prop);
        const mayBeUndefined =
          propType.isUnion() && propType.types.some((m) => m.flags & ts.TypeFlags.Undefined);
        optional ||= !!(prop.flags & ts.SymbolFlags.Optional) || mayBeUndefined;
        shapes.push(shapeOfType(propType, checker));
      }
      fields.set(name, { optional, shape: mergeShapes(shapes) });
    }
    return { fields, shape };
  };
}

/* ── 맞추기 ───────────────────────────────────────────────────────────── */

const show = (s: Iterable<string>) => [...s].sort().join(" | ");

/** `wide` 가 `narrow` 를 다 받는가. 못 받는 것을 문장으로 돌려준다. */
function notCovered(narrow: ValueShape, wide: ValueShape): string | null {
  if (narrow.any || wide.any) return null;
  const kinds = [...narrow.kinds].filter((k) => !wide.kinds.has(k));
  if (kinds.length) return show(kinds);
  // 서버가 그냥 `str` 로 둔 칸을 웹이 리터럴로 좁힌 것(또는 반대)은 비교하지 않는다 — 한쪽이 값을
  // 약속하지 않았으니 맞는지 알 수 없다. 둘 다 값을 정해 뒀을 때만 맞춘다.
  if (narrow.literals && wide.literals) {
    const values = [...narrow.literals].filter((v) => !wide.literals!.has(v));
    if (values.length) return values.map((v) => `"${v}"`).join(" | ");
  }
  if (narrow.items && wide.items) {
    const miss = notCovered(narrow.items, wide.items);
    if (miss) return `원소 ${miss}`;
  }
  return null;
}

/**
 * 서버 스키마 하나와 웹 타입 하나를 맞춘다. 어긋난 곳마다 한 줄 — 칸 이름으로 시작한다.
 * `allowed` 에 있는 칸은 건너뛴다 (대응표에 이유가 적혀 있다).
 */
export function compareSchema(
  schema: JsonSchema,
  web: WebType,
  directions: ReadonlySet<Direction>,
  spec: OpenApiSpec,
  allowed: ReadonlySet<string> = new Set(),
): string[] {
  const findings: string[] = [];
  const request = directions.has("request");
  const response = directions.has("response");

  if (schema.enum) {
    const server = shapeOfSchema(schema, spec);
    if (response) {
      const miss = notCovered(server, web.shape);
      if (miss) findings.push(`(값) 서버가 보내는데 웹 타입에 없음: ${miss}`);
    }
    if (request) {
      const miss = notCovered(web.shape, server);
      if (miss) findings.push(`(값) 웹이 보내는데 서버가 안 받음: ${miss}`);
    }
    return findings;
  }

  if (!web.fields) return ["웹 타입이 객체가 아니에요"];
  const props = schema.properties ?? {};
  const required = new Set(schema.required ?? []);

  for (const name of web.fields.keys()) {
    if (allowed.has(name) || name in props) continue;
    findings.push(
      response
        ? `${name}: 웹이 기다리는데 서버 응답에 없는 칸`
        : `${name}: 웹이 보내는데 서버가 모르는 칸`,
    );
  }

  for (const [name, prop] of Object.entries(props)) {
    if (allowed.has(name)) continue;
    const field = web.fields.get(name);
    if (!field) {
      if (request && required.has(name)) findings.push(`${name}: 서버 필수 칸인데 웹 타입에 없음`);
      continue;
    }
    if (request && required.has(name) && field.optional) {
      findings.push(`${name}: 서버 필수 칸인데 웹이 빠뜨릴 수 있음 (\`?\`)`);
    }
    const server = shapeOfSchema(prop, spec);
    if (response) {
      const miss = notCovered(server, field.shape);
      if (miss) findings.push(`${name}: 서버가 보낼 수 있는데 웹 타입이 못 받음 — ${miss}`);
    }
    if (request) {
      const miss = notCovered(field.shape, server);
      if (miss) findings.push(`${name}: 웹이 보낼 수 있는데 서버가 안 받음 — ${miss}`);
    }
  }
  return findings;
}
