/**
 * 웹 API 타입 ↔ 서버 OpenAPI (#559). 규칙은 `compare.ts` 머리말, 짝은 `schema-map.ts`.
 *
 *     make web-contract     # 서버 계약을 .openapi.json 으로 덤프한 뒤 이 파일을 돈다
 *
 * 깨졌을 때 — 웹 타입이 틀렸으면 `src/lib/api/types.ts` 를 고친다. 의도된 차이면 대응표의 `allow` 에
 * 이유를 적는다. 서버가 틀렸으면 서버 Owner 와 맞춘다 (루트 CLAUDE.md §5 — 계약 정본은 Swagger).
 */

import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

import { compareSchema, directionsOf, readWebTypes, type OpenApiSpec } from "./compare";
import { NOT_MAPPED, SCHEMA_MAP, type MapEntry } from "./schema-map";

const root = resolve(__dirname, "..");
const specPath = resolve(root, process.env.OPENAPI_JSON ?? ".openapi.json");

if (!existsSync(specPath)) {
  throw new Error(
    `${specPath} 가 없어요. 루트에서 make web-contract 로 돌리면 서버 계약을 먼저 뽑아요 (apps/api/scripts/dump_openapi.py).`,
  );
}

const spec = JSON.parse(readFileSync(specPath, "utf8")) as OpenApiSpec;
const schemas = spec.components.schemas;
const directions = directionsOf(spec);
const resolveWeb = readWebTypes(
  [
    "src/lib/api/types.ts",
    "src/lib/api/sse.ts",
    "src/lib/api/errors.ts",
    // 화면 오류 보고는 api 클라이언트를 거치지 않아서 타입이 lib/api 밖에 있다 (docs/web/api-client-v1.md §1)
    "src/lib/report-render-error.ts",
  ].map((f) => resolve(root, f)),
  resolve(root, "tsconfig.json"),
);

const entries = Object.entries(SCHEMA_MAP).flatMap(([server, value]) =>
  [value].flat().map((raw) => {
    const entry: MapEntry = typeof raw === "string" ? { web: raw } : raw;
    return { server, web: entry.web, allow: new Set(Object.keys(entry.allow ?? {})) };
  }),
);

describe("대응표가 서버 스키마를 빠짐없이 덮는다", () => {
  it("서버 스키마는 전부 SCHEMA_MAP 이나 NOT_MAPPED 에 있다", () => {
    const listed = new Set([...Object.keys(SCHEMA_MAP), ...Object.keys(NOT_MAPPED)]);
    expect(Object.keys(schemas).filter((n) => !listed.has(n))).toEqual([]);
  });

  it("대응표에 서버에 없는 스키마가 남아 있지 않다", () => {
    const stale = [...Object.keys(SCHEMA_MAP), ...Object.keys(NOT_MAPPED)].filter(
      (n) => !(n in schemas),
    );
    expect(stale).toEqual([]);
  });

  it("한 스키마가 두 곳에 같이 있지 않다", () => {
    expect(Object.keys(SCHEMA_MAP).filter((n) => n in NOT_MAPPED)).toEqual([]);
  });
});

describe("웹 타입이 서버 계약과 맞는다", () => {
  for (const { server, web, allow } of entries) {
    it(`${server} ↔ ${web}`, () => {
      const used = directions.get(server);
      // 어느 경로에도 안 걸린 스키마는 양쪽 규칙을 다 건다 — 어느 쪽으로 쓰일지 모른다.
      const dirs = used?.size ? used : new Set(["request", "response"] as const);
      expect(compareSchema(schemas[server]!, resolveWeb(web), dirs, spec, allow)).toEqual([]);
    });
  }
});
