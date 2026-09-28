"""GET /api/v1/policies/{scope}/{version} — 약관 정본 HTML 을 그대로 내준다 (#172 PR B).

동의 화면의 "전문 보기" 가 이 주소를 연다. 서버가 저장해 둔 완성본을 화면이 손대지 않고 띄우므로
"저장된 정본 = 보호자가 본 것" 이 된다 (멘토 #71-4).

🚨 로그인 없이 부른다 (루트 CLAUDE.md §9). 동의 화면은 계정이 만들어지기 전에 뜬다.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.domains.consent.models import ConsentScope
from app.domains.policy.repository import register_version
from app.main import app

TEXTS = Path(__file__).resolve().parents[3] / "alembic" / "policy_texts"


def page_url(scope: str, version: str) -> str:
    return f"/api/v1/policies/{scope}/{version}"


async def test_page_is_the_committed_html_without_login(db_client):
    """Authorization 없이 200, 저장소에 커밋한 HTML 과 한 글자도 다르지 않다."""
    response = await db_client.get(page_url("location", "draft-1"))

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert response.text == (TEXTS / "draft-1" / "location.html").read_text(encoding="utf-8")


async def test_page_cannot_run_scripts(db_client):
    """🚨 정본은 스크립트 없이 스타일만 쓴다. 헤더로도 스크립트 · 외부 불러오기를 막는다."""
    response = await db_client.get(page_url("service_terms", "draft-1"))

    policy = response.headers["content-security-policy"]
    assert "default-src 'none'" in policy
    assert "script-src" not in policy, "스크립트를 허용하는 칸을 두지 않는다"
    assert response.headers["x-content-type-options"] == "nosniff"


async def test_ended_version_is_still_readable(db_client, session):
    """끝난 옛 약관도 읽을 수 있다 — 그 글에 동의한 기록이 남아 있고 "변경 전 약관 보기" 가 된다."""
    await register_version(
        session,
        scope=ConsentScope.SERVICE_TERMS,
        version="test-ended",
        label="서비스 이용약관",
        content="옛 약관",
        content_hash="old-hash",
        content_html="<!doctype html><p>옛 약관</p>",
        effective_at=datetime.now(UTC) - timedelta(days=30),
        ended_at=datetime.now(UTC) - timedelta(days=1),
    )

    response = await db_client.get(page_url("service_terms", "test-ended"))

    assert response.status_code == 200
    assert "옛 약관" in response.text


async def test_unknown_version_is_404(db_client):
    response = await db_client.get(page_url("service_terms", "no-such-version"))

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


async def test_placeholder_without_html_is_404(db_client):
    """draft-0 은 "TODO" 자리 표시 글이라 정본 HTML 이 없다. 없는 정본을 지어내 보여 주지 않는다."""
    response = await db_client.get(page_url("child_basic", "draft-0"))

    assert response.status_code == 404


async def test_unknown_scope_is_422(db_client):
    response = await db_client.get(page_url("no_such_scope", "draft-1"))

    assert response.status_code == 422


@pytest.mark.parametrize(
    "version", ["draft%001", "a" * 65, "draft 1"], ids=["nul", "long", "space"]
)
async def test_malformed_version_is_422(db_client, version):
    """🚨 버전 이름 모양이 아니면 DB 에 묻기 전에 422 다.

    NUL 은 Postgres 가 받지 못해 조회에서 500 이 났다 (#172 리뷰).
    """
    response = await db_client.get(page_url("location", version))

    assert response.status_code == 422


def test_swagger_tells_the_screen_how_to_use_the_page():
    """API 계약의 정본은 Swagger 다. 화면이 알아야 할 것(손대지 않고 띄운다)이 거기 보여야 한다."""
    spec = app.openapi()
    html_path = spec["components"]["schemas"]["PolicyResponse"]["properties"]["html_path"]
    not_found = spec["paths"]["/api/v1/policies/{scope}/{version}"]["get"]["responses"]["404"]

    assert "손대지 않고" in html_path["description"]
    assert "application/json" in not_found["content"], "404 는 HTML 이 아니라 오류 봉투(JSON)다"


async def test_list_points_to_each_page(db_client):
    """GET /policies 가 버전마다 정본 주소를 준다. 정본이 없는 버전(draft-0)은 null 이다.

    경로는 프론트 API 클라이언트의 다른 경로와 같은 기준(`/api/v1` 아래)이다.
    """
    body = (await db_client.get("/api/v1/policies")).json()
    paths = {item["scope"]: item["html_path"] for item in body}

    assert paths["location"] == "/policies/location/draft-1"
    assert paths["service_terms"] == "/policies/service_terms/draft-1"
    assert paths["child_basic"] is None
