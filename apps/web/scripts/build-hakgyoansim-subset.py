"""학교안심 날개 R → wght 400-700 가변폰트 + 동적 서브셋 woff2 + @font-face CSS 생성.

정본 결정: docs/web/design-system-v1.md §4.

    uv run --no-project --with "fonttools[woff]" python scripts/build-hakgyoansim-subset.py

원본 TTF 는 `apps/web/fonts-src/` 에 커밋해 둔다 (1.4MB). SIL OFL 1.1 이라 변환·재배포가
허용되므로 저장소에 두는 편이 낫다 — 누구나 이 스크립트로 같은 결과를 다시 만들 수 있다.

🚨 OFL 은 재배포본에 **저작권 고지와 라이선스를 같이** 두라고 요구한다. 그래서
`public/fonts/hakgyoansim/LICENSE.txt` 를 woff2 옆에 두고, 이 스크립트가 서브셋마다
라이선스 레코드(name ID 13·14)를 심는다 — 원본 TTF 에는 그 레코드가 없어서,
파일 하나만 따로 받아 가면 조건을 알 방법이 없기 때문이다.
Reserved Font Name 선언이 없어서 굵기를 늘린 파생본을 같은 이름으로 내보내도 된다.

왜 Pretendard 의 unicode-range 파티션을 그대로 쓰나
- 그 92개 파티션은 한글 사용 빈도순이다 (block 91 = 라틴, 90 = 최빈 한글, 0 = 희귀 CJK 호환).
  브라우저는 화면에 실제 쓰인 글자가 걸리는 블록만 받는다.
- 두 폰트가 같은 경계를 쓰면, 본문 서체에 없는 글자가 Pretendard 로 떨어질 때도
  블록 단위가 어긋나지 않는다.

굵기를 우리가 만드는 이유 — 학교안심 날개는 Regular 한 벌만 배포된다
- 브라우저 합성(faux bold)은 600 이상만 걸려서 **`label`(500)이 `body`(400)와 똑같이
  나왔다**. 그리고 합성 굵기의 생김새는 엔진이 정한다.
- 그래서 원본에서 700 마스터를 만들고 `wght 400-700` 가변폰트로 굽는다. 선언한 4단계가
  전부 실물 외곽선이 되고, 합성은 아무 데서도 일어나지 않는다.
- 정적으로 여러 벌 싣는 대신 가변폰트를 고른 것은 용량 때문이다 — 굵기 한 벌마다 92개
  파티션이 통째로 하나 더 생긴다. 실측으로 정적 3벌이 x3.00, 가변폰트가 x1.59 다.
"""

from __future__ import annotations

import math
import re
import tempfile
from pathlib import Path

from fontTools.designspaceLib import AxisDescriptor, DesignSpaceDocument, SourceDescriptor
from fontTools.pens.boundsPen import BoundsPen
from fontTools.subset import main as subset_main
from fontTools.ttLib import TTFont
from fontTools.varLib import build as build_variable

WEB = Path(__file__).resolve().parent.parent
SRC = WEB / "fonts-src" / "Hakgyoansim_NalgaeR.ttf"
OUT_DIR = WEB / "public" / "fonts" / "hakgyoansim"
OUT_CSS = WEB / "src" / "app" / "hakgyoansim.css"
PRETENDARD_CSS = WEB / "src" / "app" / "pretendard.css"
REFERENCE = WEB / "public" / "fonts" / "pretendard" / "PretendardVariable.subset.90.woff2"

FAMILY = "Hakgyoansim Nalgae R"
STEM = "HakgyoansimNalgaeR.subset"

# 팽창량 (units, upm 1000). 700 마스터를 원본에서 얼마나 부풀릴지.
# 13px 에서 획이 약 +0.72px 굵어진다. 값을 바꾸면 아래 검사가 다시 돈다.
STRENGTH = 26.0
# 뾰족한 모서리에서 미터가 무한히 뻗는 것을 막는 상한.
MITER_LIMIT = 2.2
# 🚨 속공간 오프셋 상한. 좁은 속공간(ㅃ 계열 · 일부 기호)은 팽창량이 반폭을 넘으면
#    안팎이 뒤집혀 구멍이 메워진다. 얇고 긴 도형에서 면적/둘레 ~ 반폭 이므로 그 비율로 자른다.
COUNTER_CAP = 0.8

# OFL 이 요구하는 고지. 원본 TTF 의 name 테이블에 없어서 서브셋에 우리가 넣는다.
LICENSE_TEXT = (
    "This Font Software is licensed under the SIL Open Font License, Version 1.1. "
    "This license is available with a FAQ at: https://scripts.sil.org/OFL"
)
LICENSE_URL = "https://scripts.sil.org/OFL"

HEADER = """/* 학교안심 날개 R — wght 400-700 가변폰트 · 동적 서브셋 (자체 호스팅)
 *
 * 정본 결정: docs/web/design-system-v1.md §4.
 * 🚨 **손으로 고치지 않는다.** `scripts/build-hakgyoansim-subset.py` 가 만든 파일이다.
 *    폰트를 올리거나 파티션·굵기를 바꾸려면 스크립트를 다시 돌린다. prettier 대상에서도 빼 뒀다.
 *
 * size-adjust 가 왜 붙어 있나
 *   한글 글자 높이가 Pretendard 보다 작아서, 그대로 두면 §4 의 8단계가 한 단계씩
 *   작아 보이고 "caption 12px 보다 작게 쓰지 않는다" 는 하한이 사실상 깨진다.
 *   두 폰트의 한글 글자 높이를 스크립트가 실측해 맞춘다.
 *
 * 🚨 ascent-override · descent-override 가 왜 붙어 있나
 *   size-adjust 는 글자 외곽선만 키우고 브라우저가 그리는 **선택 하이라이트 밴드**는
 *   같이 커지지 않는다. 보정하지 않으면 글자를 드래그했을 때 밴드가 글자 윗부분을
 *   덮지 못하고 잘린 것처럼 보인다 (앞 폰트에서 실제로 그렇게 보였다).
 *   폰트의 원래 ascent·descent 에 size-adjust 비율을 곱해 되돌린다.
 *
 * 굵기가 400-700 연속이다
 *   원본은 Regular 한 벌뿐이라, 스크립트가 700 마스터를 만들어 가변폰트로 굽는다.
 *   §4 의 400·500·600·700 이 전부 실물 외곽선이고 **브라우저 합성은 일어나지 않는다.**
 *   `font-weight: 400 700` 이 그 범위를 알리는 선언이다 — 이게 빠지면 브라우저가
 *   400 단일로 보고 600 이상을 다시 합성한다.
 *
 * 라이선스: public/fonts/hakgyoansim/LICENSE.txt
 */
"""


def hangul_height(path: Path) -> float:
    font = TTFont(path, lazy=True)
    upm = font["head"].unitsPerEm
    glyphs, cmap = font.getGlyphSet(), font.getBestCmap()
    heights = []
    for char in "한글가나빛음":
        name = cmap.get(ord(char))
        if name is None:
            continue
        pen = BoundsPen(glyphs)
        glyphs[name].draw(pen)
        if pen.bounds:
            heights.append((pen.bounds[3] - pen.bounds[1]) / upm)
    return sum(heights) / len(heights)


def _unit(dx: float, dy: float) -> tuple[float, float] | None:
    length = math.hypot(dx, dy)
    return (dx / length, dy / length) if length > 1e-9 else None


def _contour_area_perimeter(coords, points) -> tuple[float, float]:
    area = perimeter = 0.0
    count = len(points)
    for k in range(count):
        x0, y0 = coords[points[k]]
        x1, y1 = coords[points[(k + 1) % count]]
        area += x0 * y1 - x1 * y0
        perimeter += math.hypot(x1 - x0, y1 - y0)
    return area / 2.0, perimeter


def embolden_glyph(glyph, glyf, strength: float) -> None:
    """좌표를 법선 방향으로 밀어 획을 굵게 한다. **점 개수·윤곽선 구조는 그대로 둔다** —
    varLib 보간은 두 마스터의 점이 1:1 로 대응해야만 성립한다. (윤곽선을 합집합으로
    부풀리는 방법은 점 개수가 달라져서 쓸 수 없다.)"""
    if glyph.numberOfContours <= 0:  # 빈 글리프 · 컴포지트
        return
    coords = glyph.coordinates
    new = list(coords)
    start = 0
    for end in glyph.endPtsOfContours:
        points = list(range(start, end + 1))
        start = end + 1
        count = len(points)
        if count < 3:
            continue

        area, perimeter = _contour_area_perimeter(coords, points)
        # 🚨 윤곽선 방향을 정규화하지 않는다. TrueType nonzero 는 바깥 윤곽선이 시계방향
        #    (면적 < 0) · 속공간이 반시계방향(면적 > 0)이라, **모든 윤곽선에 같은 회전**을
        #    걸면 바깥은 팽창하고 속공간은 수축한다 = 획이 굵어진다.
        #    윤곽선마다 "자기 바깥쪽" 으로 밀면 속공간이 같이 벌어져 ㅇ·ㅁ 만 얇아 보인다.
        offset = strength
        if area > 0 and perimeter > 1e-9:
            offset = min(strength, COUNTER_CAP * area / perimeter)

        for k in range(count):
            i_prev, i_cur, i_next = points[k - 1], points[k], points[(k + 1) % count]
            xp, yp = coords[i_prev]
            xc, yc = coords[i_cur]
            xn, yn = coords[i_next]
            vin = _unit(xc - xp, yc - yp)
            vout = _unit(xn - xc, yn - yc)
            if vin is None and vout is None:
                continue
            vin = vin or vout
            vout = vout or vin

            n_in = (-vin[1], vin[0])
            n_out = (-vout[1], vout[0])
            bisector = _unit(n_in[0] + n_out[0], n_in[1] + n_out[1])
            if bisector is None:  # 180도 꺾임 — 미터를 포기하고 들어온 방향을 쓴다
                bisector, scale = n_in, 1.0
            else:
                cos_half = bisector[0] * n_in[0] + bisector[1] * n_in[1]
                scale = min(1.0 / cos_half, MITER_LIMIT) if cos_half > 1e-6 else MITER_LIMIT

            new[i_cur] = (
                round(xc + bisector[0] * offset * scale),
                round(yc + bisector[1] * offset * scale),
            )
    glyph.coordinates = type(coords)(new)
    glyph.recalcBounds(glyf)


def _contour_areas(glyph) -> list[float]:
    areas, start = [], 0
    for end in glyph.endPtsOfContours:
        points = list(range(start, end + 1))
        areas.append(_contour_area_perimeter(glyph.coordinates, points)[0])
        start = end + 1
    return areas


def verify(light: TTFont, bold: TTFont) -> None:
    """전수 검사 — 하나라도 어긋나면 빌드를 멈춘다. 눈으로는 12,636 글리프를 못 본다."""
    lo, hi = light["glyf"], bold["glyf"]
    mismatched = inverted = thinner = spiked = 0
    spike_limit = STRENGTH * MITER_LIMIT + 2

    for name in light.getGlyphOrder():
        a, b = lo[name], hi[name]
        if a.numberOfContours <= 0:
            continue
        if len(a.coordinates) != len(b.coordinates):
            mismatched += 1
            continue
        for x, y in zip(_contour_areas(a), _contour_areas(b)):
            if x < 0 and abs(y) <= abs(x):  # 바깥 윤곽선인데 안 커졌다
                thinner += 1
            elif x > 0 and y <= 0:  # 속공간이 뒤집혔다 = 구멍이 메워진다
                inverted += 1
        grow = max(a.xMin - b.xMin, b.xMax - a.xMax, a.yMin - b.yMin, b.yMax - a.yMax)
        if grow > spike_limit:
            spiked += 1

    problems = {
        "점 개수가 다른 글리프 (보간 불가)": mismatched,
        "바깥 윤곽선이 안 굵어진 곳": thinner,
        "속공간이 뒤집힌 곳 (구멍이 메워진다)": inverted,
        "모서리 미터 스파이크": spiked,
    }
    for label, count in problems.items():
        if count:
            raise SystemExit(f"팽창 검사 실패 — {label}: {count}개 (STRENGTH={STRENGTH})")
    print(f"팽창 검사 통과 — {len(light.getGlyphOrder()):,} 글리프 · STRENGTH {STRENGTH:g}")


def build_variable_font(out: Path) -> None:
    """원본(400) 과 팽창 마스터(700) 로 wght 축 가변폰트를 만든다."""
    bold = TTFont(SRC)
    glyf = bold["glyf"]
    for name in bold.getGlyphOrder():
        embolden_glyph(glyf[name], glyf, STRENGTH)
    bold["OS/2"].usWeightClass = 700

    verify(TTFont(SRC, lazy=True), bold)

    doc = DesignSpaceDocument()
    axis = AxisDescriptor()
    axis.name, axis.tag = "Weight", "wght"
    axis.minimum, axis.default, axis.maximum = 400, 400, 700
    axis.labelNames = {"en": "Weight"}
    doc.addAxis(axis)
    for weight, font in ((400, TTFont(SRC)), (700, bold)):
        source = SourceDescriptor()
        source.font, source.name = font, f"master{weight}"
        source.location = {"Weight": weight}
        if weight == 400:  # 이름·메타데이터는 원본에서 가져온다
            source.copyInfo = source.copyLib = source.copyGroups = source.copyFeatures = True
        doc.addSource(source)

    variable, _, _ = build_variable(doc)
    variable.save(out)


def _stamp_license(path: Path) -> None:
    """서브셋에 라이선스 레코드(name ID 13·14)를 심는다. 저작권(0)은 서브셋이 이미 유지한다."""
    font = TTFont(path)
    name = font["name"]
    for platform_id, enc_id, lang_id in ((3, 1, 0x409), (1, 0, 0)):
        name.setName(LICENSE_TEXT, 13, platform_id, enc_id, lang_id)
        name.setName(LICENSE_URL, 14, platform_id, enc_id, lang_id)
    font.flavor = "woff2"
    font.save(path)


def main() -> None:
    if not SRC.exists():
        raise SystemExit(f"원본 TTF 가 없다: {SRC}")

    blocks = re.findall(
        r"\[(\d+)\][\s\S]*?unicode-range:\s*([^;]+);",
        PRETENDARD_CSS.read_text(encoding="utf-8"),
    )
    if not blocks:
        raise SystemExit(f"파티션을 못 읽었다: {PRETENDARD_CSS}")

    adjust = round(hangul_height(REFERENCE) / hangul_height(SRC) * 100)

    # 선택 밴드 보정 — 폰트 원래 수직 지표에 size-adjust 비율을 곱해 되돌린다
    src_font = TTFont(SRC, lazy=True)
    upm = src_font["head"].unitsPerEm
    hhea = src_font["hhea"]
    scale = adjust / 100
    ascent = round(hhea.ascent / upm * scale * 100, 1)
    descent = round(-hhea.descent / upm * scale * 100, 1)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for stale in OUT_DIR.glob(f"{STEM}.*.woff2"):
        stale.unlink()

    with tempfile.TemporaryDirectory() as tmp:
        # 가변폰트 원본은 파생물이라 커밋하지 않는다 — 서브셋만 저장소에 남는다
        variable = Path(tmp) / "HakgyoansimNalgae-VF.ttf"
        build_variable_font(variable)

        faces, total = [], 0
        for index, unicode_range in blocks:
            out = OUT_DIR / f"{STEM}.{index}.woff2"
            subset_main([
                str(variable),
                "--unicodes=" + unicode_range.replace("U+", "").replace(" ", ""),
                "--flavor=woff2",
                f"--output-file={out}",
                "--layout-features=*",
                "--no-hinting",
                "--desubroutinize",
            ])
            _stamp_license(out)
            total += out.stat().st_size
            faces.append(
                f"/* [{index}] */\n"
                f"@font-face {{\n"
                f"\tfont-family: '{FAMILY}';\n"
                f"\tfont-style: normal;\n"
                f"\tfont-display: swap;\n"
                f"\tfont-weight: 400 700;\n"
                f"\tsize-adjust: {adjust}%;\n"
                f"\tascent-override: {ascent}%;\n"
                f"\tdescent-override: {descent}%;\n"
                f'\tsrc: url("/fonts/hakgyoansim/{out.name}") '
                f"format('woff2-variations');\n"
                f"\tunicode-range: {unicode_range.strip()};\n"
                f"}}\n"
            )

    OUT_CSS.write_text(HEADER + "\n" + "\n".join(faces), encoding="utf-8")
    print(
        f"{len(faces)}개 · {total / 1024:.0f}KB · wght 400-700 · size-adjust {adjust}% · "
        f"ascent {ascent}% / descent {descent}% → {OUT_CSS}"
    )


if __name__ == "__main__":
    main()
