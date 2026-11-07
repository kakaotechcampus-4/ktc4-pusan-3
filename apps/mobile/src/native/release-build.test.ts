/**
 * 릴리스 빌드의 R8 설정 (#692).
 *
 * `android/` 는 생성물이라 R8 을 켜는 곳은 `app.json` 의 `expo-build-properties` 하나다. 이 줄이 빠지면 빌드는
 * 그대로 통과하고 Play Console 만 "난독화 비율 1%" 로 경고해서, 놓치기 쉽다.
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

type PluginEntry = string | [string, Record<string, unknown>];

const plugins = (): PluginEntry[] =>
  JSON.parse(readFileSync(new URL("../../app.json", import.meta.url), "utf8")).expo.plugins;

test("릴리스 빌드는 R8 과 리소스 축소를 켠다", () => {
  const entry = plugins().find(
    (p): p is [string, Record<string, unknown>] => Array.isArray(p) && p[0] === "expo-build-properties",
  );
  assert.ok(entry, "app.json plugins 에 expo-build-properties 가 옵션과 함께 있어야 한다");
  // 두 키만 본다. keep 규칙(`extraProguardRules`) 같은 옵션이 더해져도 R8 이 켜져 있으면 통과해야 한다
  const android = entry[1].android as Record<string, unknown> | undefined;
  assert.equal(android?.enableMinifyInReleaseBuilds, true, "R8(코드 축소 · 난독화)이 꺼졌다");
  assert.equal(android?.enableShrinkResourcesInReleaseBuilds, true, "리소스 축소가 꺼졌다");
});
