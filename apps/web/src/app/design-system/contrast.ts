/**
 * WCAG 2.2 대비비 계산. 디자인 시스템 §10 이 "대비비는 눈으로 고르지 않고 계산한다" 라고
 * 정해 뒀는데, 그 계산이 문서에만 있고 코드에는 없었다. 이 화면이 **실제 토큰 값**으로
 * 다시 계산해서, 토큰을 바꾸면 그 자리에서 통과/실패가 드러나게 한다.
 *
 * 공식: sRGB → linearize → 0.2126R + 0.7152G + 0.0722B (§10)
 */

export type Rgba = { r: number; g: number; b: number; a: number };

/**
 * `#221f1b` · `#000000d9` · `#abc` · `rgb(34, 31, 27)` · `rgb(0 0 0 / 0.85)` 를 모두 받는다.
 *
 * 🚨 **hex 를 반드시 다뤄야 한다.** CSS 커스텀 속성은 `getPropertyValue` 로 읽으면
 *    작성한 그대로(= hex) 돌아온다 — `rgb()` 로 정규화되는 건 `color` 같은 실제 속성뿐이다.
 *    처음엔 숫자만 훑는 파서를 썼다가 hex 를 전부 못 읽었고, 그 `null` 이 "통과" 로 집계돼
 *    **검사가 통째로 헛돌았다.** 읽지 못하면 조용히 넘기지 말고 null 을 돌려주고,
 *    호출부가 그걸 "측정 실패" 로 드러내야 한다.
 */
export function parseColor(value: string): Rgba | null {
  const v = value.trim();
  if (!v) return null;

  if (v.startsWith("#")) {
    const hex = v.slice(1);
    const expand = (h: string) => (h.length <= 4 ? [...h].map((c) => c + c).join("") : h);
    const full = expand(hex);
    if (full.length !== 6 && full.length !== 8) return null;
    if (!/^[0-9a-f]+$/i.test(full)) return null;
    const byte = (i: number) => parseInt(full.slice(i, i + 2), 16);
    return {
      r: byte(0),
      g: byte(2),
      b: byte(4),
      a: full.length === 8 ? byte(6) / 255 : 1,
    };
  }

  const nums = v.match(/[\d.]+%?/g);
  if (!nums || nums.length < 3) return null;
  const toByte = (s: string) => (s.endsWith("%") ? (parseFloat(s) / 100) * 255 : parseFloat(s));
  const [r, g, b] = nums.slice(0, 3).map(toByte);
  const a = nums[3] === undefined ? 1 : parseFloat(nums[3]) / (nums[3].endsWith("%") ? 100 : 1);
  return { r, g, b, a };
}

/** 반투명 색은 배경 위에 얹은 결과로 바꿔서 잰다 — kakao-ink 가 검정 85% 다. */
export function composite(fg: Rgba, bg: Rgba): Rgba {
  return {
    r: fg.r * fg.a + bg.r * (1 - fg.a),
    g: fg.g * fg.a + bg.g * (1 - fg.a),
    b: fg.b * fg.a + bg.b * (1 - fg.a),
    a: 1,
  };
}

function luminance({ r, g, b }: Rgba): number {
  const lin = (v: number) => {
    const c = v / 255;
    return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
}

export function contrastRatio(foreground: Rgba, background: Rgba): number {
  const fg = foreground.a < 1 ? composite(foreground, background) : foreground;
  const [hi, lo] = [luminance(fg), luminance(background)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

/** 본문 4.5:1 · 비텍스트(테두리·아이콘) 3:1 (§2 · §10). */
export function meetsAA(ratio: number, kind: "text" | "non-text"): boolean {
  return ratio >= (kind === "text" ? 4.5 : 3);
}
