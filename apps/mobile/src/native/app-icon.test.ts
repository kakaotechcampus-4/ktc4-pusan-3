/**
 * 앱 아이콘 · 알림 아이콘 · 스토어 아이콘 · 웹 파비콘 생성물이 스토어와 런처가 받는 모양인지 (#695).
 * 그림 자체는 `scripts/build-icons.py` 가 만든다 — 여기서는 손으로 바꿔 넣었을 때 깨지는 조건만 본다.
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { inflateSync } from "node:zlib";

const MOBILE = new URL("../../", import.meta.url);
const WEB_APP = new URL("../web/src/app/", MOBILE);

type PngHeader = { width: number; height: number; colorType: number; hasTrns: boolean };

function pngHeader(file: URL): PngHeader {
  const buf = readFileSync(file);
  assert.equal(buf.subarray(1, 4).toString("latin1"), "PNG", `${file.pathname} 이 PNG 가 아니다`);
  let hasTrns = false;
  // 청크를 따라가며 tRNS(팔레트 · RGB 의 투명색)가 있는지 본다.
  for (let at = 8; at < buf.length; ) {
    const len = buf.readUInt32BE(at);
    const type = buf.subarray(at + 4, at + 8).toString("latin1");
    if (type === "tRNS") hasTrns = true;
    if (type === "IDAT") break;
    at += 12 + len;
  }
  return {
    width: buf.readUInt32BE(16),
    height: buf.readUInt32BE(20),
    colorType: buf[25],
    hasTrns,
  };
}

/** 8비트 RGBA · 비인터레이스 PNG 의 픽셀 (스크립트가 내는 모양만 읽는다). `rgba` 는 픽셀마다 4바이트다. */
function pngPixels(file: URL): { width: number; height: number; rgba: Uint8Array; alpha: Uint8Array } {
  const buf = readFileSync(file);
  const { width, height, colorType } = pngHeader(file);
  assert.equal(colorType, 6, `${file.pathname} 이 RGBA 가 아니다`);
  assert.equal(buf[24], 8, "비트 깊이 8 만 읽는다");
  assert.equal(buf[28], 0, "인터레이스는 읽지 않는다");
  const idat: Buffer[] = [];
  for (let at = 8; at < buf.length; ) {
    const len = buf.readUInt32BE(at);
    if (buf.subarray(at + 4, at + 8).toString("latin1") === "IDAT") idat.push(buf.subarray(at + 8, at + 8 + len));
    at += 12 + len;
  }
  const raw = inflateSync(Buffer.concat(idat));
  const bpp = 4;
  const stride = width * bpp;
  const px = new Uint8Array(stride * height);
  for (let y = 0; y < height; y++) {
    const filter = raw[y * (stride + 1)];
    const src = raw.subarray(y * (stride + 1) + 1, (y + 1) * (stride + 1));
    for (let i = 0; i < stride; i++) {
      const a = i >= bpp ? px[y * stride + i - bpp] : 0;
      const b = y > 0 ? px[(y - 1) * stride + i] : 0;
      const c = y > 0 && i >= bpp ? px[(y - 1) * stride + i - bpp] : 0;
      let pred = 0;
      if (filter === 1) pred = a;
      else if (filter === 2) pred = b;
      else if (filter === 3) pred = (a + b) >> 1;
      else if (filter === 4) {
        const p = a + b - c;
        const pa = Math.abs(p - a);
        const pb = Math.abs(p - b);
        const pc = Math.abs(p - c);
        pred = pa <= pb && pa <= pc ? a : pb <= pc ? b : c;
      }
      px[y * stride + i] = (src[i] + pred) & 0xff;
    }
  }
  const alpha = new Uint8Array(width * height);
  for (let i = 0; i < alpha.length; i++) alpha[i] = px[i * bpp + 3];
  return { width, height, rgba: px, alpha };
}

function appConfig() {
  return JSON.parse(readFileSync(new URL("app.json", MOBILE), "utf8")).expo;
}

function notificationsPlugin(): { icon?: string; color?: string } {
  const entry = appConfig().plugins.find(
    (p: unknown) => Array.isArray(p) && p[0] === "expo-notifications",
  );
  assert.ok(entry, "app.json 에 expo-notifications 플러그인이 없다");
  return entry[1];
}

// 2 = RGB, 0 = 회색. 4 · 6 은 알파 채널이 있다.
const OPAQUE_COLOR_TYPES = [0, 2];

test("iOS 아이콘은 투명 픽셀이 없다 — App Store 가 알파 채널이 있는 아이콘을 받지 않는다", () => {
  for (const [file, size] of [
    [new URL("assets/icon.png", MOBILE), 1024],
    [new URL("apple-icon.png", WEB_APP), 180],
  ] as const) {
    const png = pngHeader(file);
    assert.ok(OPAQUE_COLOR_TYPES.includes(png.colorType), `${file.pathname} colorType ${png.colorType}`);
    assert.equal(png.hasTrns, false, `${file.pathname} 에 tRNS 가 있다`);
    assert.deepEqual([png.width, png.height], [size, size]);
  }
});

test("적응형 아이콘은 전경과 테마 실루엣이 같은 크기라 같은 자리에 놓인다", () => {
  const adaptive = appConfig().android.adaptiveIcon;
  // 배경은 그림이 아니라 색 하나다. 스크립트의 흰 바탕(`WHITE`)과 iOS 아이콘의 바탕이 같은 색이다.
  assert.equal(adaptive.backgroundImage, undefined);
  assert.equal(adaptive.backgroundColor, "#FFFFFF");

  const fg = pngHeader(new URL(adaptive.foregroundImage, MOBILE));
  const mono = pngHeader(new URL(adaptive.monochromeImage, MOBILE));
  assert.deepEqual([fg.width, fg.height], [mono.width, mono.height]);
  assert.equal(fg.width, fg.height);
});

test("적응형 아이콘의 로고는 지름 66dp 안전 원 안에 있다 — 원형 런처에서 주황 점이 잘리지 않게", () => {
  const adaptive = appConfig().android.adaptiveIcon;
  for (const path of [adaptive.foregroundImage, adaptive.monochromeImage]) {
    const { width, height, alpha } = pngPixels(new URL(path, MOBILE));
    // 108dp 그림에서 어떤 마스크든 남는 가운데 원 (지름 66dp)
    const safe = (66 / 108 / 2) * width;
    let outside = 0;
    let inside = 0;
    for (let y = 0; y < height; y++) {
      for (let x = 0; x < width; x++) {
        // 안티에일리어싱의 옅은 가장자리는 빼고 센다 — 잘려서 티가 나는 것은 보이는 픽셀이다
        if (alpha[y * width + x] <= 8) continue;
        if (Math.hypot(x + 0.5 - width / 2, y + 0.5 - height / 2) > safe) outside++;
        else inside++;
      }
    }
    assert.ok(inside > 0, `${path} 에 그림이 없다`);
    assert.equal(outside, 0, `${path} 의 픽셀 ${outside}개가 안전 원 밖에 있다`);
  }
});

test("알림 아이콘은 흰색 하나에 투명 바탕이다 — 흰색이 아니면 그대로 그리는 기기가 있다", () => {
  const { icon } = notificationsPlugin();
  assert.ok(icon, "플러그인 icon 이 비어 있다");
  const { width, height, rgba, alpha } = pngPixels(new URL(icon, MOBILE));
  // 플러그인이 24dp 기준으로 줄인다 — 가장 큰 밀도(xxxhdpi · 4배) 이상이어야 흐려지지 않는다
  assert.deepEqual([width, height], [96, 96]);
  let drawn = 0;
  let transparent = 0;
  for (let i = 0; i < alpha.length; i++) {
    if (alpha[i] === 0) {
      transparent++;
      continue;
    }
    drawn++;
    assert.deepEqual([rgba[i * 4], rgba[i * 4 + 1], rgba[i * 4 + 2]], [255, 255, 255], `픽셀 ${i} 가 흰색이 아니다`);
  }
  assert.ok(drawn > 0 && transparent > 0, "실루엣이 아니다 (비었거나 꽉 찼다)");
});

test("알림 강조색은 로고 초록이다 — 토큰 brand 가 아니라 그림의 일부다", () => {
  const logo = readFileSync(new URL("assets/logo.svg", MOBILE), "utf8");
  const green = logo.match(/<g id="green-layer" fill="(#[0-9A-Fa-f]{6})">/)?.[1];
  assert.ok(green, "logo.svg 에서 green-layer 를 못 찾았다");
  assert.equal(notificationsPlugin().color?.toUpperCase(), green.toUpperCase());
});

test("Play 등록 정보 아이콘은 512 32비트 PNG 이고 전부 불투명하다", () => {
  const file = new URL("store/play-icon.png", MOBILE);
  // Play Console 은 32비트(RGBA) 만 받는다. 모서리는 Play 가 깎으므로 투명한 모서리를 그려 넣지 않는다
  const { width, height, alpha } = pngPixels(file);
  assert.deepEqual([width, height], [512, 512]);
  assert.ok(alpha.every((a) => a === 255), "투명한 픽셀이 있다");
});
