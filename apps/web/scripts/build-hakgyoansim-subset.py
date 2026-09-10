"""학교안심 날개 R → 동적 서브셋 woff2 + @font-face CSS 생성.

정본 결정: docs/web/design-system-v1.md §4.

    uv run --no-project --with "fonttools[woff]" python scripts/build-hakgyoansim-subset.py

원본 TTF 는 `apps/web/fonts-src/` 에 커밋해 둔다 (1.4MB). SIL OFL 1.1 이라 변환·재배포가
허용되므로 저장소에 두는 편이 낫다 — 누구나 이 스크립트로 같은 결과를 다시 만들 수 있다.

🚨 OFL 은 재배포본에 **저작권 고지와 라이선스를 같이** 두라고 요구한다. 그래서
`public/fonts/hakgyoansim/LICENSE.txt` 를 woff2 옆에 두고, 이 스크립트가 서브셋마다
라이선스 레코드(name ID 13·14)를 심는다 — 원본 TTF 에는 그 레코드가 없어서,
파일 하나만 따로 받아 가면 조건을 알 방법이 없기 때문이다.

왜 Pretendard 의 unicode-range 파티션을 그대로 쓰나
- 그 92개 파티션은 한글 사용 빈도순이다 (block 91 = 라틴, 90 = 최빈 한글, 0 = 희귀 CJK 호환).
  브라우저는 화면에 실제 쓰인 글자가 걸리는 블록만 받는다.
- 두 폰트가 같은 경계를 쓰면, 본문 서체에 없는 글자가 Pretendard 로 떨어질 때도
  블록 단위가 어긋나지 않는다.
"""

from __future__ import annotations

import re
from pathlib import Path

from fontTools.pens.boundsPen import BoundsPen
from fontTools.subset import main as subset_main
from fontTools.ttLib import TTFont

WEB = Path(__file__).resolve().parent.parent
SRC = WEB / "fonts-src" / "Hakgyoansim_NalgaeR.ttf"
OUT_DIR = WEB / "public" / "fonts" / "hakgyoansim"
OUT_CSS = WEB / "src" / "app" / "hakgyoansim.css"
PRETENDARD_CSS = WEB / "src" / "app" / "pretendard.css"
REFERENCE = WEB / "public" / "fonts" / "pretendard" / "PretendardVariable.subset.90.woff2"

FAMILY = "Hakgyoansim Nalgae R"
STEM = "HakgyoansimNalgaeR.subset"

# OFL 이 요구하는 고지. 원본 TTF 의 name 테이블에 없어서 서브셋에 우리가 넣는다.
LICENSE_TEXT = (
    "This Font Software is licensed under the SIL Open Font License, Version 1.1. "
    "This license is available with a FAQ at: https://scripts.sil.org/OFL"
)
LICENSE_URL = "https://scripts.sil.org/OFL"

HEADER = """/* 학교안심 날개 R — 동적 서브셋 (자체 호스팅)
 *
 * 정본 결정: docs/web/design-system-v1.md §4.
 * 🚨 **손으로 고치지 않는다.** `scripts/build-hakgyoansim-subset.py` 가 만든 파일이다.
 *    폰트를 올리거나 파티션을 바꾸려면 스크립트를 다시 돌린다. prettier 대상에서도 빼 뒀다.
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
 * 굵기가 하나뿐이다
 *   Regular 400 단일 웨이트다. §4 의 600~700 은 브라우저가 합성(faux bold)한다.
 *   ⚠️ 500 은 합성되지 않아 400 과 똑같이 나온다 — `label` 이 `body` 와 구분되지 않는다.
 *   법률 문서 화면은 이 합성을 피하려고 `--font-doc`(Pretendard) 를 쓴다.
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

    faces, total = [], 0
    for index, unicode_range in blocks:
        out = OUT_DIR / f"{STEM}.{index}.woff2"
        subset_main([
            str(SRC),
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
            f"\tfont-weight: 400;\n"
            f"\tsize-adjust: {adjust}%;\n"
            f"\tascent-override: {ascent}%;\n"
            f"\tdescent-override: {descent}%;\n"
            f'\tsrc: url("/fonts/hakgyoansim/{out.name}") format(\'woff2\');\n'
            f"\tunicode-range: {unicode_range.strip()};\n"
            f"}}\n"
        )

    OUT_CSS.write_text(HEADER + "\n" + "\n".join(faces), encoding="utf-8")
    print(
        f"{len(faces)}개 · {total / 1024:.0f}KB · size-adjust {adjust}% · "
        f"ascent {ascent}% / descent {descent}% → {OUT_CSS}"
    )


if __name__ == "__main__":
    main()
