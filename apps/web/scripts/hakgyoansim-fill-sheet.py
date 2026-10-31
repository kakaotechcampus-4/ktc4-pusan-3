"""빈 한글 채우기 결과를 사람이 훑어보는 검수 시트 (PNG 여러 장) · 재현 검사.

정본 결정: docs/web/design-system-v1.md §4 (원본의 빈 한글).

    uv run --no-project --with "fonttools[woff]" --with freetype-py --with pillow \\
        python scripts/hakgyoansim-fill-sheet.py [출력 폴더]
    uv run --no-project --with "fonttools[woff]" --with freetype-py --with pillow \\
        python scripts/hakgyoansim-fill-sheet.py --check

시트 — 채운 글자를 칸마다 **글자 폭대로 두 번 이어** 그리고(간격이 이상하면 바로 보인다),
아래에 Pretendard 로 정답 글자를 적는다. 제 글자로 읽히지 않는 것이 보이면
`hakgyoansim_fill.py` 의 `EXCLUDE` 에 넣고 빌드를 다시 돌린다.
출력은 저장소 밖(기본: 임시 폴더)에 둔다 — 커밋하지 않는다.

`--check` — 문서에 적은 재현 검사 수치를 다시 낸다. 원본이 그린 글자를 하나씩 가리고 나머지만으로
다시 만들어, 판정을 통과한 것의 래스터 IoU 를 원본과 비교한다. 판정 기준값을 바꾸면 돌려서
문서의 숫자를 고친다.
"""

from __future__ import annotations

import io
import random
import re
import statistics
import sys
import tempfile
from pathlib import Path

import hakgyoansim_fill as hf
from fontTools.pens.freetypePen import FreeTypePen
from fontTools.pens.pointPen import PointToSegmentPen
from fontTools.ttLib import TTFont
from PIL import Image, ImageChops, ImageDraw, ImageFont

WEB = Path(__file__).resolve().parent.parent
SRC = WEB / "fonts-src" / "Hakgyoansim_NalgaeR.ttf"
PRETENDARD_CSS = WEB / "src" / "app" / "pretendard.css"
PRETENDARD_DIR = WEB / "public" / "fonts" / "pretendard"

CELL, LABEL, COLS, ROWS = 64, 28, 16, 12
SCALE = CELL / 1100  # units → px (upm 1000 + 위아래 여유)
CELL_WIDTH = round(1800 * SCALE)  # 글자 두 개 폭

CHECK_SAMPLES, CHECK_SEED = 600, 11


def _pretendard_fonts(size: int):
    """코드포인트 → Pretendard 서브셋 폰트. woff2 는 PIL 이 못 읽어 TTF 로 풀어 둔다."""
    blocks = re.findall(
        r"src: url\([^)]*?(PretendardVariable\.subset\.\d+\.woff2)[^;]*;"
        r"\s*unicode-range:\s*([^;]+);",
        PRETENDARD_CSS.read_text(encoding="utf-8"),
    )
    by_cp, cache = {}, {}
    for name, ranges in blocks:
        for part in ranges.replace("U+", "").replace(" ", "").split(","):
            first, _, last = part.partition("-")
            for cp in range(int(first, 16), int(last or first, 16) + 1):
                by_cp[cp] = name

    def font_for(cp: int):
        name = by_cp[cp]
        if name not in cache:
            font = TTFont(PRETENDARD_DIR / name)
            font.flavor = None
            buf = io.BytesIO()
            font.save(buf)
            buf.seek(0)
            cache[name] = ImageFont.truetype(buf, size)
        return cache[name]

    return font_for


def _mask(contours, offsets=(0,), width=CELL) -> Image.Image:
    """외곽선을 (x 오프셋마다 한 번씩) 그린 알파 마스크."""
    pen = FreeTypePen(None)
    points = PointToSegmentPen(pen)
    for dx in offsets:
        for pts, flags in contours:
            points.beginPath()
            for (x, y), on in zip(pts, flags, strict=True):
                points.addPoint((x + dx, y), "qcurve" if on else None)
            points.endPath()
    image = pen.image(
        width=width,
        height=CELL,
        transform=(SCALE, 0, 0, SCALE, 0, 250 * SCALE),
        contain=False,
    )
    return image.split()[-1]


def sheet(out_dir: Path) -> None:
    font = TTFont(SRC)
    filled = sorted(hf.fill_hangul(font, exclude=hf.EXCLUDE).filled)
    glyf, cmap, hmtx = font["glyf"], font.getBestCmap(), font["hmtx"]
    label_font = _pretendard_fonts(LABEL - 6)

    per_page = COLS * ROWS
    for page in range(0, len(filled), per_page):
        chunk = filled[page : page + per_page]
        image = Image.new("RGB", (COLS * CELL_WIDTH, ROWS * (CELL + LABEL)), "white")
        draw = ImageDraw.Draw(image)
        for i, cp in enumerate(chunk):
            x, y = (i % COLS) * CELL_WIDTH, (i // COLS) * (CELL + LABEL)
            contours = hf._contours(glyf[cmap[cp]], glyf)
            mask = _mask(contours, offsets=(0, hmtx[cmap[cp]][0]), width=CELL_WIDTH)
            tile = Image.new("RGB", mask.size, (255, 244, 230))
            tile.paste((20, 20, 20), mask=mask)
            image.paste(tile, (x, y))
            draw.text(
                (x + CELL_WIDTH // 2 - 10, y + CELL + 1),
                chr(cp),
                fill=(110, 110, 110),
                font=label_font(cp),
            )
        image.save(out_dir / f"fill-{page // per_page + 1:02d}.png")
    pages = (len(filled) + per_page - 1) // per_page
    print(f"{len(filled):,}자 · {pages}장 → {out_dir}")


def check() -> None:
    drawn, _ = hf.read_drawn(TTFont(SRC))
    parts = hf._clean(hf.label(drawn))
    keys = random.Random(CHECK_SEED).sample(sorted(drawn), CHECK_SAMPLES)

    ious, rejected, missing = [], 0, 0
    for key in keys:
        pieces = hf.Composer({k: p for k, p in parts.items() if k != key}).compose(key)
        if pieces is None:
            missing += 1
            continue
        if hf.judge(pieces):
            rejected += 1
            continue
        made = _mask([c for p in pieces for c in hf.fit(p)]).point(lambda v: 255 if v > 127 else 0)
        real = _mask(drawn[key]).point(lambda v: 255 if v > 127 else 0)
        both = ImageChops.multiply(made, real).histogram()[255]
        either = ImageChops.lighter(made, real).histogram()[255]
        ious.append(both / max(either, 1))

    ious.sort()
    print(
        f"재현 검사 {CHECK_SAMPLES}자 · 통과 {len(ious)} · 판정 탈락 {rejected}"
        f" · 재료 없음 {missing}"
        f" · IoU 중앙값 {statistics.median(ious):.3f} · 하위 10% {ious[len(ious) // 10]:.3f}"
    )


def main() -> None:
    if sys.argv[1:] == ["--check"]:
        check()
        return
    out_dir = (
        Path(sys.argv[1]) if len(sys.argv) > 1 else Path(tempfile.mkdtemp(prefix="hakgyoansim-"))
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    sheet(out_dir)


if __name__ == "__main__":
    main()
