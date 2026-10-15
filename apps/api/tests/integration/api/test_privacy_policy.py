"""GET /api/v1/privacy-policy — 개인정보 처리방침 정본 HTML (#221).

스토어 등록 정보 · 가입 화면 · 설정이 고정 주소를 건다. 서버는 커밋한 HTML 을 그대로 내준다.

🚨 로그인 없이 연다 (루트 CLAUDE.md §9). 처리방침은 동의 대상이 아니라서
   동의 목록(GET /policies)에 나오지 않아야 한다 — 나오면 가입 화면이 체크박스로 그린다.
"""

from scripts.render_policy_html import render_policy_html
from scripts.render_privacy_policy import PRIVACY, TITLE

LATEST_URL = "/api/v1/privacy-policy"


def committed(version: str) -> str:
    return (PRIVACY / f"{version}.html").read_text(encoding="utf-8")


async def test_fixed_url_is_the_newest_version_without_login(client):
    """Authorization 없이 200, 시행일이 가장 늦은 정본과 한 글자도 다르지 않다."""
    newest = max(page.stem for page in PRIVACY.glob("*.html"))

    response = await client.get(LATEST_URL)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert response.text == committed(newest)


async def test_version_url_is_that_version(client):
    response = await client.get(f"{LATEST_URL}/2026-10-15")

    assert response.status_code == 200
    assert response.text == committed("2026-10-15")


async def test_unknown_version_is_404(client):
    response = await client.get(f"{LATEST_URL}/2020-01-01")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


async def test_version_must_be_a_date(client):
    """시행일 모양이 아니면 파일을 찾기 전에 막는다 — 경로 이름이 파일 이름으로 쓰이는 곳이다."""
    response = await client.get(f"{LATEST_URL}/draft-1")

    assert response.status_code == 422


async def test_pages_cannot_run_scripts(client):
    """🚨 약관 정본과 같은 헤더 — 스크립트 · 외부 불러오기를 막는다."""
    for url in (LATEST_URL, f"{LATEST_URL}/2026-10-15"):
        response = await client.get(url)

        policy = response.headers["content-security-policy"]
        assert "default-src 'none'" in policy
        assert "script-src" not in policy
        assert response.headers["x-content-type-options"] == "nosniff"


async def test_not_offered_as_a_consent(db_client):
    """🚨 동의 목록에 처리방침이 없다 — 있으면 "처리방침에 동의" 체크박스가 생긴다."""
    response = await db_client.get("/api/v1/policies")

    assert response.status_code == 200
    for item in response.json():
        assert "privacy-policy" not in (item["html_path"] or "")
        assert item["scope"] != "privacy_policy"


def test_committed_html_is_rendered_from_the_source():
    """커밋한 HTML 이 같은 시행일 원고를 약관과 같은 변환기로 만든 그대로다.

    원고만 고치고 HTML 을 다시 만들지 않으면 서버는 옛 글을 내준다. 반대로 HTML 만 손으로 고치면
    원고와 정본이 갈린다.
    """
    sources = sorted(PRIVACY.glob("*.md"))
    assert sources, "privacy_policy/<시행일>.md 가 하나도 없다"
    for source in sources:
        expected = render_policy_html(
            source.read_text(encoding="utf-8"), title=TITLE, version=source.stem
        )
        assert committed(source.stem) == expected, f"{source.name} 을 다시 렌더링해야 한다"
