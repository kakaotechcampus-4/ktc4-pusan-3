"""개인정보 처리방침 원고(마크다운) → 정본 HTML (#221).

    cd apps/api && uv run python -m scripts.render_privacy_policy 2026-10-15

`app/domains/policy/privacy_policy/<시행일>.md` 옆에 `<시행일>.html` 을 만든다. 변환기는 약관 정본과
같다(`scripts/render_policy_html`) — 모양 · 스크립트 금지 · 해시 표시가 약관 페이지와 같아진다.

약관과 다른 점 — 처리방침은 동의를 받는 글이 아니라서 DB(`policy_version`)에 넣지 않는다. 서버가 이
파일을 그대로 내준다 (`GET /privacy-policy`). 버전 이름은 시행일이다 (처리방침 15장 ③).

🚨 이미 공개한 시행일의 HTML 은 다시 만들지 않는다 — 그날 게시한 글이 바뀌면 안 된다. HTML 이 이미
   있으면 멈춘다. 아직 공개(머지) 전인 원고를 고쳐 다시 만들 때만 `--force` 를 쓴다.
"""

import argparse
import sys
from pathlib import Path

from scripts.render_policy_html import render_policy_html

PRIVACY = Path(__file__).resolve().parents[1] / "app" / "domains" / "policy" / "privacy_policy"

TITLE = "아이캐치 개인정보 처리방침"


def render_privacy(version: str, *, force: bool = False) -> Path:
    """한 시행일의 원고를 HTML 로. 만든 파일 경로를 돌려준다."""
    source = PRIVACY / f"{version}.md"
    target = source.with_suffix(".html")
    if target.exists() and not force:
        raise FileExistsError(
            f"privacy_policy/{target.name} 가 이미 있다. 공개한 버전이면 새 시행일 원고를 만든다. "
            "아직 공개 전인 원고를 고친 것이면 --force."
        )
    page = render_policy_html(source.read_text(encoding="utf-8"), title=TITLE, version=version)
    target.write_text(page, encoding="utf-8")
    return target


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("version", help="시행일 (예: 2026-10-15) — privacy_policy/<시행일>.md")
    parser.add_argument(
        "--force", action="store_true", help="이미 있는 HTML 을 덮어쓴다. 공개 전 원고에만 쓴다"
    )
    args = parser.parse_args()

    if not (PRIVACY / f"{args.version}.md").is_file():
        print(f"privacy_policy/{args.version}.md 가 없다", file=sys.stderr)
        return 1
    try:
        written = render_privacy(args.version, force=args.force)
    except FileExistsError as error:
        print(error, file=sys.stderr)
        return 1
    print(written.relative_to(PRIVACY.parents[3]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
