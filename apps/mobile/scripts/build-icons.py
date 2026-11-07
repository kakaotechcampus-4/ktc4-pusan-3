"""앱 아이콘과 웹 파비콘 — 로고 한 장에서 전부 만든다 (#695).

    uv run --no-project --with resvg-py==0.5.0 --with pillow==13.0.0 python scripts/build-icons.py

판을 고정한 이유 — 생성물을 커밋하므로, 로고를 안 바꿨는데 렌더러 판이 바뀌어 PNG 가 달라지면 안 된다.

입력
- `assets/logo.svg` — 로고 원본 (C 심볼 · 아이 얼굴 · 주황 점, 글자 없음). 흰 바탕 사각형은 빼고 쓴다

출력 (앱)
- `assets/icon.png` — 1024 불투명 PNG. 흰 바탕을 꽉 채운다. iOS 와 Android 7 이하가 쓴다.
  🚨 iOS 는 투명 픽셀이 있는 아이콘을 받지 않는다 — 그래서 RGB 로 저장한다
- `assets/android-icon-foreground.png` — 1024(= 108dp) 투명 PNG. 적응형 아이콘의 전경. 배경은 `app.json` 의 흰색
- `assets/android-icon-monochrome.png` — 같은 자리의 한 색 실루엣. Android 13+ 테마 아이콘이 이 모양에 색을 입힌다
- `assets/favicon.png` — 48 (Expo `web.favicon`). 웹 파비콘과 같은 그림
- `assets/notification-icon.png` — 96(= 24dp × 4) 흰 실루엣. `expo-notifications` 플러그인 `icon` 이 밀도별로 줄인다.
  Android 는 알림 아이콘의 알파만 보고 색은 플러그인 `color` 로 입힌다 — 그래도 흰색이어야 하는 런처 · 제조사가 있다

출력 (스토어 · 빌드에 들어가지 않는다)
- `store/play-icon.png` — 512 Play Console 등록 정보 아이콘. `icon.png` 와 같은 그림이고 모서리 · 그림자는 Play 가 입힌다.
  Play 는 32비트 PNG 를 요구해서 알파 채널을 둔다 (전부 불투명)

출력 (웹 · `apps/web/src/app/` — Next App Router 가 파일 이름으로 `<link rel="icon">` 을 단다)
- `icon.svg` · `favicon.ico`(16 · 32 · 48) — 흰 둥근 사각형 위에 로고. 어두운 탭에서도 초록이 묻히지 않게
- `apple-icon.png` — 180 불투명. iOS 홈 화면 추가

🚨 **적응형 아이콘의 로고는 지름 66dp 원 안에 있어야 한다.** 런처는 108dp 그림을 원 · 둥근 사각형 · 물방울 같은
   제 모양으로 잘라 가운데 72dp 를 보여 주고, 어떤 모양이든 남는 것이 가운데 지름 66dp 원이다. 그래서 크기는
   bbox 가 아니라 로고 외곽선 점이 전부 들어가는 원으로 잡는다 (`fit`). 원 반지름을 바꾸면 런처에서 주황 점이 먼저 잘린다.
"""

from __future__ import annotations

import io
import math
import re
from dataclasses import dataclass
from pathlib import Path

import resvg_py
from PIL import Image

MOBILE = Path(__file__).resolve().parent.parent
ASSETS = MOBILE / "assets"
WEB_APP = MOBILE.parent / "web" / "src" / "app"
LOGO = ASSETS / "logo.svg"
STORE = MOBILE / "store"

WHITE = "#FFFFFF"
# 테마 아이콘은 알파만 본다. 색은 미리보기용이다.
MONO = "#000000"
# 로고의 세 색. 다시 저장한 SVG 에서 그룹 하나를 못 읽으면 오류 없이 다른 그림이 나오므로 셋 다 있는지 본다.
LOGO_FILLS = {"#266746", "#FC8732", "#FEC7A7"}
# 볼(분홍)은 실루엣에서 뺀다 — 얼굴 안에 덩어리로 남아 눈 · 입과 붙어 보인다.
MONO_SKIP = {"#FEC7A7"}

MASTER = 1024
# 로고를 감싸는 원의 반지름 : 그림 한 변. 그림마다 잘리는 방식이 달라서 따로 둔다.
ADAPTIVE_RADIUS = 0.295  # 66dp / 108dp / 2 = 0.3056 에서 안티에일리어싱 몫만큼 안쪽
NOTIFICATION_RADIUS = 0.41  # 24dp 칸의 안쪽 20dp(가장자리 2dp 여백) 지름 20/24/2 = 0.4167 에서 조금 안쪽
FULL_BLEED_RADIUS = 0.38  # iOS 둥근 모서리 · 홈 화면 격자에서 다른 앱 아이콘과 비슷한 무게
TILE_RADIUS = 0.43  # 파비콘 — 16px 에서 남는 게 거의 없어서 크게 둔다
# 파비콘 흰 바탕의 모서리 반지름 : 한 변
TILE_CORNER = 0.22


@dataclass(frozen=True)
class Logo:
    paths: list[tuple[str, str]]  # (채움색, d)
    cx: float
    cy: float
    reach: float  # (cx, cy) 에서 가장 먼 외곽선 점까지


def load_logo() -> Logo:
    """`logo.svg` 의 path 들과, 그 점을 전부 담는 가장 작은 원 (흰 바탕 사각형은 뺀다)."""
    svg = LOGO.read_text(encoding="utf-8")
    paths: list[tuple[str, str]] = []
    for group in re.finditer(r'<g id="[^"]+" fill="(#[0-9A-Fa-f]{6})">(.*?)</g>', svg, re.S):
        fill, body = group.group(1).upper(), group.group(2)
        for d in re.findall(r'\sd="([^"]+)"', body):
            paths.append((fill, d))
    if {fill for fill, _ in paths} != LOGO_FILLS:
        raise SystemExit(f"로고 색이 {sorted(LOGO_FILLS)} 가 아니다: {sorted({f for f, _ in paths})} ({LOGO})")
    # 외접원은 절대 좌표 쌍만 읽는다. 편집기가 상대 명령(소문자)이나 다른 명령으로 다시 쓰면 원이 틀어진다.
    other = {cmd for _, d in paths for cmd in re.findall(r"[A-Za-z]", d)} - {"M", "Q", "Z"}
    if other:
        raise SystemExit(f"M · Q · Z 밖의 path 명령이 있다: {sorted(other)} — 절대 좌표로 다시 저장한다 ({LOGO})")
    pts = [
        (float(x), float(y))
        for _, d in paths
        for x, y in re.findall(r"(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)", d)
    ]

    def reach(cx: float, cy: float) -> float:
        return max(math.hypot(x - cx, y - cy) for x, y in pts)

    # 최소 외접원 중심 — 볼록 함수라 좁혀 가는 격자 탐색으로 충분하다.
    cx = (min(x for x, _ in pts) + max(x for x, _ in pts)) / 2
    cy = (min(y for _, y in pts) + max(y for _, y in pts)) / 2
    step = 64.0
    while step > 0.05:
        cx, cy = min(
            ((cx + dx * step, cy + dy * step) for dx in (-1, 0, 1) for dy in (-1, 0, 1)),
            key=lambda c: reach(*c),
        )
        step /= 2
    return Logo(paths, cx, cy, reach(cx, cy))


def compose(
    logo: Logo,
    size: int,
    radius: float,
    *,
    background: str | None = None,
    tile: bool = False,
    mono: str | None = None,
    title: str | None = None,
) -> str:
    """로고를 `size` 정사각형 가운데에, 외접원 반지름이 `radius * size` 가 되게 놓은 SVG."""
    scale = radius * size / logo.reach
    transform = (
        f"translate({size / 2:g} {size / 2:g}) scale({scale:.6f}) "
        f"translate({-logo.cx:.3f} {-logo.cy:.3f})"
    )
    body = "\n".join(
        f'    <path fill="{mono or fill}" fill-rule="evenodd" d="{d}"/>'
        for fill, d in logo.paths
        if not (mono and fill in MONO_SKIP)
    )
    back = ""
    if tile:
        r = TILE_CORNER * size
        back = f'  <rect width="{size}" height="{size}" rx="{r:g}" fill="{WHITE}"/>\n'
    elif background:
        back = f'  <rect width="{size}" height="{size}" fill="{background}"/>\n'
    head = f"<title>{title}</title>\n" if title else ""
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" viewBox="0 0 {size} {size}">\n'
        f"{head}"
        "<!-- 🚨 손으로 고치지 않는다. apps/mobile/scripts/build-icons.py 가 assets/logo.svg 로 만든다. -->\n"
        f"{back}"
        f'  <g transform="{transform}">\n{body}\n  </g>\n'
        "</svg>\n"
    )


def render(svg: str, px: int) -> Image.Image:
    png = bytes(resvg_py.svg_to_bytes(svg_string=svg, width=px, height=px))
    return Image.open(io.BytesIO(png)).convert("RGBA")


def save_png(img: Image.Image, path: Path, *, opaque: bool = False) -> None:
    if opaque:
        img = img.convert("RGB")
    img.save(path, format="PNG", optimize=True)
    print(f"→ {path.relative_to(MOBILE.parent.parent)} ({img.width}px{' 불투명' if opaque else ''})")


def main() -> None:
    logo = load_logo()

    full_bleed = compose(logo, MASTER, FULL_BLEED_RADIUS, background=WHITE)
    adaptive = compose(logo, MASTER, ADAPTIVE_RADIUS)
    mono = compose(logo, MASTER, ADAPTIVE_RADIUS, mono=MONO)
    notification = compose(logo, MASTER, NOTIFICATION_RADIUS, mono=WHITE)
    tile = compose(logo, MASTER, TILE_RADIUS, tile=True, title="아이캐치")

    save_png(render(full_bleed, 1024), ASSETS / "icon.png", opaque=True)
    save_png(render(adaptive, 1024), ASSETS / "android-icon-foreground.png")
    save_png(render(mono, 1024), ASSETS / "android-icon-monochrome.png")
    save_png(render(tile, 48), ASSETS / "favicon.png")
    save_png(render(notification, 96), ASSETS / "notification-icon.png")
    STORE.mkdir(exist_ok=True)
    save_png(render(full_bleed, 512), STORE / "play-icon.png")

    (WEB_APP / "icon.svg").write_text(tile, encoding="utf-8")
    print(f"→ {(WEB_APP / 'icon.svg').relative_to(MOBILE.parent.parent)}")
    save_png(render(full_bleed, 180), WEB_APP / "apple-icon.png", opaque=True)
    # 크기마다 따로 그린다 — 큰 그림을 줄이면 16px 에서 선이 뭉개진다.
    ico_sizes = [16, 32, 48]
    frames = [render(tile, px) for px in ico_sizes]
    frames[-1].save(
        WEB_APP / "favicon.ico",
        format="ICO",
        sizes=[(px, px) for px in ico_sizes],
        append_images=frames[:-1],
    )
    print(f"→ {(WEB_APP / 'favicon.ico').relative_to(MOBILE.parent.parent)} ({' · '.join(map(str, ico_sizes))})")

    print(f"외접원 중심 ({logo.cx:.1f}, {logo.cy:.1f}) · 반지름 {logo.reach:.1f} (logo.svg 좌표)")


if __name__ == "__main__":
    main()
