"""약관 정본 HTML — 원고(마크다운)에서 한 번 만들어 저장소에 커밋한다 (#172 PR B).

멘토 #71-4: 사용자가 실제로 본 것을 증명하려면 서버가 최종 렌더한 정본을 저장하고 화면은 그것을
변형 없이 표시해야 한다. 여기서 지키는 것 셋.
    ① 원고 안의 HTML 은 글자로 나간다 — 정본에 스크립트가 끼어들 길이 없다
    ② 정본은 스스로 완결된다 — 스크립트 · 외부 파일 없이 스타일까지 안에 있다. 나중에 디자인을
       바꿔도 옛 버전의 모양이 따라 바뀌지 않는다
    ③ 커밋된 HTML 은 옆의 원고에서 만든 것이다 — 원고 해시가 HTML 안에 새겨져 있다
"""

import hashlib
import re
from pathlib import Path

import pytest

from scripts.render_policy_html import render_policy_html, render_version

TEXTS = Path(__file__).resolve().parents[3] / "alembic" / "policy_texts"
COMMITTED_HTML = sorted(TEXTS.glob("*/*.html"))


def source_hash_in(html: str) -> str:
    match = re.search(r'<meta name="policy-source-sha256" content="([0-9a-f]{64})">', html)
    assert match, "원고 해시 표시가 없다"
    return match.group(1)


def test_raw_html_in_the_text_is_escaped():
    """① 원고에 태그를 적어도 태그가 아니라 글자로 보인다."""
    text = "안내 <script>alert(1)</script> <img src=x onerror=alert(1)>"

    html = render_policy_html(text, title="t", version="v")

    assert "<script" not in html
    assert "<img" not in html
    assert "&lt;script&gt;" in html


def test_page_is_self_contained():
    """② 한 파일로 완결 — 외부 스타일 · 스크립트 · 이미지를 부르지 않는다."""
    html = render_policy_html(
        "# 제목\n\n| 가 | 나 |\n| --- | --- |\n| 1 | 2 |\n", title="약관", version="v"
    )

    assert html.startswith("<!doctype html>")
    assert '<html lang="ko">' in html
    assert '<meta charset="utf-8">' in html
    assert "<title>약관</title>" in html
    assert "<style>" in html
    assert "<table>" in html, "표가 표로 그려진다 (약관의 보유 기간 표)"
    assert not re.search(r"<(script|link|iframe|img)\b", html)
    assert not re.search(r"(src|href)=\"https?:", html)


def test_same_text_gives_the_same_page():
    """재현 가능해야 정본이다 — 날짜 같은 실행마다 바뀌는 값을 넣지 않는다."""
    text = "## 제1조\n\n본문\n"

    assert render_policy_html(text, title="t", version="v") == render_policy_html(
        text, title="t", version="v"
    )


def test_source_hash_is_embedded():
    """③ 이 HTML 을 어느 원고에서 만들었는지가 HTML 안에 남는다."""
    text = "본문\n"

    html = render_policy_html(text, title="t", version="v")

    assert source_hash_in(html) == hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_page_says_what_it_is():
    """웹뷰에 이 페이지만 떠도 무슨 문서의 어느 버전인지 보인다."""
    html = render_policy_html("본문\n", title="위치정보 수집·이용", version="draft-1")

    assert "<h1>위치정보 수집·이용</h1>" in html
    assert "draft-1" in html


def test_there_are_committed_pages():
    assert COMMITTED_HTML, "policy_texts/<버전>/<scope>.html 이 하나도 없다"


def test_every_text_has_its_page():
    """원고만 넣고 HTML 을 안 만들면 여기서 깨진다 (scripts/render_policy_html.py 로 만든다)."""
    missing = [p for p in TEXTS.glob("*/*.md") if not p.with_suffix(".html").exists()]

    assert missing == []


def test_existing_pages_are_not_overwritten_without_force():
    """🚨 등록한 정본은 다시 만들지 않는다 — 변환기가 바뀌면 이미 동의받은 글의 모양이 바뀐다."""
    with pytest.raises(FileExistsError):
        render_version("draft-1")


@pytest.mark.parametrize("path", COMMITTED_HTML, ids=lambda p: f"{p.parent.name}/{p.stem}")
def test_committed_page_was_made_from_its_text(path: Path):
    """③ 커밋된 HTML 의 원고 해시 = 옆 원고 파일의 해시. 원고만 고치고 HTML 을 안 만들면 깨진다."""
    text = path.with_suffix(".md").read_text(encoding="utf-8")
    html = path.read_text(encoding="utf-8")

    assert source_hash_in(html) == hashlib.sha256(text.encode("utf-8")).hexdigest()
    assert not re.search(r"<(script|link|iframe|img)\b", html)
