"""약관 원고(마크다운) → 정본 HTML (#172 PR B).

    cd apps/api && uv run python -m scripts.render_policy_html draft-2

`alembic/policy_texts/<버전>/<scope>.md` 마다 옆에 `<scope>.html` 을 만든다. 만든 HTML 은 저장소에
커밋하고, 마이그레이션이 그 파일을 그대로 `policy_version.content_html` 에 넣는다. 서버는 이 HTML 을
`GET /policies/{scope}/{version}` 으로 내줄 뿐 다시 만들지 않는다.

왜 저장하나 (멘토 #71-4) — 원문만 내려보내고 화면이 그리면, 화면이 문단을 숨기거나 순서를 바꿔도
서버는 모른다. 서버가 만든 완성본을 저장하고 화면은 그것을 손대지 않고 띄우면 "저장된 정본 =
보호자가 본 것" 이 된다. 보여 줄 때마다 새로 만들지 않는 이유도 같다 — 변환기 버전이 올라가 모양이
조금 달라져도, 이미 동의받은 옛 약관은 그때 그대로 남아야 한다.

🚨 원고 안의 HTML 은 글자로 내보낸다 (`html: False`). 정본에 스크립트가 끼어들 길을 만들지 않는다.
🚨 한 파일로 완결한다. 스타일은 안에 넣고 스크립트 · 외부 파일 · 이미지를 부르지 않는다 — 나중에
   디자인을 바꿔도 옛 버전의 모양이 따라 바뀌지 않는다.
🚨 날짜처럼 실행마다 바뀌는 값을 넣지 않는다. 같은 원고는 늘 같은 HTML 이다.
🚨 이미 등록한 버전의 HTML 은 다시 만들지 않는다 — 마이그레이션이 해시로 고정해 두었다. 그래서
   HTML 이 이미 있으면 멈춘다. 아직 등록 전인 버전을 고쳐 다시 만들 때만 `--force` 를 쓴다.
"""

import argparse
import hashlib
import html
import json
import sys
from pathlib import Path

from markdown_it import MarkdownIt

TEXTS = Path(__file__).resolve().parents[1] / "alembic" / "policy_texts"

_MARKDOWN = MarkdownIt("commonmark", {"html": False}).enable("table")

_STYLE = """
:root { color-scheme: light dark; --fg: #1f2328; --muted: #59636e; --line: #d1d9e0; --bg: #ffffff; }
@media (prefers-color-scheme: dark) {
  :root { --fg: #e6edf3; --muted: #9198a1; --line: #3d444d; --bg: #0d1117; }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--fg);
  font: 16px/1.7 -apple-system, BlinkMacSystemFont, "Apple SD Gothic Neo", "Noto Sans KR",
    "Malgun Gothic", sans-serif;
  word-break: keep-all; overflow-wrap: anywhere; }
main { max-width: 720px; margin: 0 auto; padding: 24px 16px 48px; }
h1, h2, h3 { line-height: 1.4; }
h1 { margin: 0; font-size: 1.4em; }
.version { margin: .25em 0 0; color: var(--muted); font-size: .9em; }
h2 { margin-top: 2em; padding-bottom: .3em; border-bottom: 1px solid var(--line);
  font-size: 1.25em; }
h3 { margin-top: 1.6em; font-size: 1.05em; }
hr { border: 0; border-top: 1px solid var(--line); margin: 2.5em 0; }
table { display: block; overflow-x: auto; border-collapse: collapse; margin: 1em 0;
  font-size: .95em; }
th, td { border: 1px solid var(--line); padding: 8px 10px; text-align: left; vertical-align: top; }
th { color: var(--muted); font-weight: 600; }
"""


def render_policy_html(markdown: str, *, title: str, version: str) -> str:
    """원고 한 벌 → 한 파일로 완결된 HTML 문서. 원고 해시를 meta 에 새긴다.

    맨 위에 문서 이름과 버전을 적는다 — 웹뷰에 이 페이지만 떠도 무엇의 어느 버전인지 보여야 한다.
    """
    source_hash = hashlib.sha256(markdown.encode("utf-8")).hexdigest()
    body = _MARKDOWN.render(markdown)
    return (
        "<!doctype html>\n"
        '<html lang="ko">\n'
        "<head>\n"
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f'<meta name="policy-source-sha256" content="{source_hash}">\n'
        f"<title>{html.escape(title)}</title>\n"
        f"<style>{_STYLE}</style>\n"
        "</head>\n"
        "<body>\n"
        "<main>\n"
        f"<header><h1>{html.escape(title)}</h1>"
        f'<p class="version">버전 {html.escape(version)}</p></header>\n'
        f"{body}</main>\n"
        "</body>\n"
        "</html>\n"
    )


def render_version(version: str, *, force: bool = False) -> list[Path]:
    """한 버전 폴더의 원고를 전부 HTML 로. 만든 파일 경로를 돌려준다.

    HTML 이 하나라도 이미 있으면 아무것도 쓰지 않고 FileExistsError — 등록된 정본을 덮어쓰지 않는다.
    """
    folder = TEXTS / version
    sources = sorted(folder.glob("*.md"))
    existing = [s.with_suffix(".html") for s in sources if s.with_suffix(".html").exists()]
    if existing and not force:
        names = ", ".join(p.name for p in existing)
        raise FileExistsError(
            f"policy_texts/{version} 에 이미 HTML 이 있다 ({names}). "
            "등록된 버전이면 새 버전 폴더를 만든다. 아직 등록 전인 버전을 고친 것이면 --force."
        )
    # 제목은 그 버전의 meta.json 에서 — 보호자가 체크박스 옆에서 본 제목과 같은 글이다.
    titles = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
    written = []
    for source in sources:
        page = render_policy_html(
            source.read_text(encoding="utf-8"), title=titles[source.stem]["label"], version=version
        )
        target = source.with_suffix(".html")
        target.write_text(page, encoding="utf-8")
        written.append(target)
    return written


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("version", help="policy_texts 아래 버전 폴더 이름 (예: draft-2)")
    parser.add_argument(
        "--force", action="store_true", help="이미 있는 HTML 을 덮어쓴다. 등록 전 버전에만 쓴다"
    )
    args = parser.parse_args()

    if not (TEXTS / args.version).is_dir():
        print(f"policy_texts/{args.version} 폴더가 없다", file=sys.stderr)
        return 1
    try:
        written = render_version(args.version, force=args.force)
    except FileExistsError as error:
        print(error, file=sys.stderr)
        return 1
    for path in written:
        print(path.relative_to(TEXTS))
    return 0


if __name__ == "__main__":
    sys.exit(main())
