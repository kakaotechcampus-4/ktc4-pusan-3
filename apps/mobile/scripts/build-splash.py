"""앱 스플래시 그림 — 로고 아래에 "아이캐치" 글자를 넣은 SVG 와 PNG 를 만든다 (#690).

    uv run --no-project --with "fonttools[woff]" --with resvg-py python scripts/build-splash.py

입력
- `assets/logo.svg` — 로고 원본 (C 심볼 · 아이 얼굴 · 주황 점, 글자 없음). 흰 바탕 사각형은 빼고 쓴다
- 웹의 학교안심 날개 가변폰트 서브셋 (`apps/web/public/fonts/hakgyoansim/`) — 앱 본문 서체를 700 으로 뽑는다

출력
- `assets/splash-logo.svg` — 글자를 외곽선으로 바꾼 한 장. 폰트 없이 어디서든 같은 모양으로 열린다
- `assets/splash-icon.png` — 1024 정사각 투명 PNG. `app.json` 의 `expo-splash-screen` 이 이 파일을 쓴다

🚨 **그림 전체가 정사각형에 내접한 원 안에 들어가야 한다.** Android 12+ 스플래시는 아이콘을 원으로 잘라
   그린다 — 아이콘 배경이 없으면 288dp 칸 가운데 지름 192dp 원이다. 플러그인은 이 PNG 를 `imageWidth`(dp)
   정사각형으로 그 칸 가운데에 놓으므로, `imageWidth` 를 192 로 두고 그림을 내접원 안에 두면 잘리지 않는다.
   그래서 이 스크립트는 로고와 글자의 외곽선 점이 전부 `SAFE_RADIUS` 안에 오도록 크기를 정한다.
"""

from __future__ import annotations

import math
import re
from pathlib import Path

import resvg_py
from fontTools.pens.recordingPen import DecomposingRecordingPen
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont

MOBILE = Path(__file__).resolve().parent.parent
LOGO = MOBILE / "assets" / "logo.svg"
FONT_DIR = MOBILE.parent / "web" / "public" / "fonts" / "hakgyoansim"
OUT_SVG = MOBILE / "assets" / "splash-logo.svg"
OUT_PNG = MOBILE / "assets" / "splash-icon.png"

WORDMARK = "아이캐치"
WEIGHT = 700
# 로고 초록 (`logo.svg` 의 green-layer). 웹의 --color-brand 가 아니라 로고의 색이다.
WORDMARK_COLOR = "#266746"

SIZE = 1024
# 내접원 반지름(512)에서 조금 안쪽 — 외곽선이 원에 닿으면 마스크 가장자리의 안티에일리어싱에 깎인다.
SAFE_RADIUS = 0.46 * SIZE
# 글자 폭 : 로고 폭. 참고 시안은 글자가 로고보다 넓다. 원 안에 넣어야 해서 그보다 좁힌다.
WORDMARK_WIDTH_RATIO = 1.05
# 로고와 글자 사이 : 로고 높이
GAP_RATIO = 0.07
# 글자 사이 간격 (em). 굵은 둥근 글자라 기본 간격이 넓어 보여 살짝 좁힌다.
TRACKING = -0.02


def logo_paths() -> tuple[list[tuple[str, str]], list[tuple[float, float]]]:
    """`logo.svg` 의 path 들 (채움색, d) 과 모든 좌표점. 흰 바탕 사각형은 뺀다."""
    svg = LOGO.read_text(encoding="utf-8")
    paths: list[tuple[str, str]] = []
    for group in re.finditer(r'<g id="[^"]+" fill="(#[0-9A-Fa-f]{6})">(.*?)</g>', svg, re.S):
        fill, body = group.group(1), group.group(2)
        for d in re.findall(r'\sd="([^"]+)"', body):
            paths.append((fill, d))
    points = [
        (float(x), float(y))
        for _, d in paths
        for x, y in re.findall(r"(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)", d)
    ]
    return paths, points


def wordmark_fonts(text: str) -> dict[str, TTFont]:
    """글자마다 그 글자를 가진 서브셋을 700 으로 뽑는다. 서브셋은 사용 빈도로 나뉘어 있어 글자가 흩어진다."""
    fonts: dict[str, TTFont] = {}
    for path in sorted(FONT_DIR.glob("*.woff2")):
        missing = [ch for ch in text if ch not in fonts]
        if not missing:
            break
        font = TTFont(path)
        cmap = font.getBestCmap()
        hits = [ch for ch in missing if ord(ch) in cmap]
        if hits:
            instance = instantiateVariableFont(font, {"wght": WEIGHT})
            for ch in hits:
                fonts[ch] = instance
    missing = [ch for ch in text if ch not in fonts]
    if missing:
        raise SystemExit(f"서브셋에 없는 글자: {missing} ({FONT_DIR})")
    return fonts


def wordmark_outline(text: str) -> DecomposingRecordingPen:
    """글자 외곽선 (폰트 단위, y 위로)."""
    fonts = wordmark_fonts(text)
    pen: DecomposingRecordingPen | None = None
    x = 0.0
    for i, ch in enumerate(text):
        font = fonts[ch]
        name = font.getBestCmap()[ord(ch)]
        glyphs = font.getGlyphSet()
        glyph_pen = DecomposingRecordingPen(glyphs)
        glyphs[name].draw(glyph_pen)
        if pen is None:
            pen = DecomposingRecordingPen(glyphs)
        glyph_pen.replay(TransformPen(pen, (1, 0, 0, 1, x, 0)))
        x += font["hmtx"][name][0]
        if i < len(text) - 1:
            x += TRACKING * font["head"].unitsPerEm
    assert pen is not None
    return pen


def recorded_points(pen: DecomposingRecordingPen) -> list[tuple[float, float]]:
    return [pt for _, args in pen.value for pt in args if isinstance(pt, tuple)]


def main() -> None:
    paths, logo_pts = logo_paths()
    text_pen = wordmark_outline(WORDMARK)
    text_pts = recorded_points(text_pen)

    # 로고 좌표계(logo.svg 의 viewBox)에서 글자를 놓는다.
    lx0 = min(x for x, _ in logo_pts)
    lx1 = max(x for x, _ in logo_pts)
    ly0 = min(y for _, y in logo_pts)
    ly1 = max(y for _, y in logo_pts)
    logo_w, logo_h = lx1 - lx0, ly1 - ly0

    tx0 = min(x for x, _ in text_pts)
    tx1 = max(x for x, _ in text_pts)
    ty0 = min(y for _, y in text_pts)
    ty1 = max(y for _, y in text_pts)
    k = WORDMARK_WIDTH_RATIO * logo_w / (tx1 - tx0)
    # 폰트 단위(y 위로) → 로고 좌표(y 아래로). 글자 윗변이 로고 아랫변 + 간격에 온다.
    text_tf = (
        k,
        0,
        0,
        -k,
        (lx0 + lx1) / 2 - k * (tx0 + tx1) / 2,
        ly1 + GAP_RATIO * logo_h + k * ty1,
    )

    def place_text(pt: tuple[float, float]) -> tuple[float, float]:
        a, b, c, d, e, f = text_tf
        return (a * pt[0] + c * pt[1] + e, b * pt[0] + d * pt[1] + f)

    all_pts = logo_pts + [place_text(pt) for pt in text_pts]

    # 원 안에 가장 크게 들어가는 중심을 찾는다. 가로는 로고 가운데로 두고(글자가 가운데 정렬이라) 세로만 찾는다.
    cx = (lx0 + lx1) / 2
    gy0 = min(y for _, y in all_pts)
    gy1 = max(y for _, y in all_pts)

    def reach(cy: float) -> float:
        return max(math.hypot(x - cx, y - cy) for x, y in all_pts)

    steps = 400
    cy = min((gy0 + (gy1 - gy0) * i / steps for i in range(steps + 1)), key=reach)
    scale = SAFE_RADIUS / reach(cy)

    # 로고 좌표 → 출력 좌표 (SIZE 정사각형, 가운데가 원 중심)
    group = f"translate({SIZE / 2:.3f} {SIZE / 2:.3f}) scale({scale:.6f}) translate({-cx:.3f} {-cy:.3f})"

    text_svg = SVGPathPen(None, ntos=lambda v: f"{v:.1f}".rstrip("0").rstrip("."))
    text_pen.replay(TransformPen(text_svg, text_tf))

    body = "\n".join(f'    <path fill="{fill}" fill-rule="evenodd" d="{d}"/>' for fill, d in paths)
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="{SIZE}" height="{SIZE}" viewBox="0 0 {SIZE} {SIZE}" role="img" aria-labelledby="title">
<title id="title">아이캐치</title>
<!-- 🚨 손으로 고치지 않는다. scripts/build-splash.py 가 assets/logo.svg 와 학교안심 날개 {WEIGHT} 으로 만든다. -->
  <g transform="{group}">
{body}
    <path id="wordmark" fill="{WORDMARK_COLOR}" d="{text_svg.getCommands()}"/>
  </g>
</svg>
"""
    OUT_SVG.write_text(svg, encoding="utf-8")
    OUT_PNG.write_bytes(bytes(resvg_py.svg_to_bytes(svg_string=svg, width=SIZE, height=SIZE)))

    print(f"scale {scale:.4f} · 그림 폭 {(max(x for x, _ in all_pts) - min(x for x, _ in all_pts)) * scale:.0f}px / {SIZE}")
    print(f"→ {OUT_SVG.relative_to(MOBILE)} · {OUT_PNG.relative_to(MOBILE)}")


if __name__ == "__main__":
    main()
