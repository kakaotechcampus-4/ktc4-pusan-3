"""GET /api/v1/policies — 동의 화면이 그릴 약관을 서버가 내려준다 (#91).

왜 필요한가 — 동의 화면이 정책 버전을 프론트 상수로 들고 있으면 DB 에 등록된 버전과
어긋난다. 실제로 화면은 `2026-09-01` 을 보내고 DB 에는 `draft-0` 뿐이라 신규 가입이
전부 `400 policy_version_invalid` 로 막혔다 (멘토 #71 P1). 화면이 버전과 본문을 여기서
받아 그대로 쓰면 둘이 어긋날 수 없다.

🚨 로그인 없이 부른다. 동의 화면은 계정이 만들어지기 전에 뜬다 (루트 CLAUDE.md §9).
"""

from datetime import UTC, datetime

from app.domains.consent.models import ConsentScope
from app.domains.policy.repository import register_version

URL = "/api/v1/policies"
DISPLAY_ORDER = ["service_terms", "privacy_account", "child_basic", "child_health"]


async def test_policies_are_readable_without_login(db_client):
    """Authorization 헤더 없이 200. 가입 전 동의 화면이 부른다."""
    response = await db_client.get(URL)

    assert response.status_code == 200


async def test_policies_come_in_display_order_with_labels(db_client):
    """화면에 보여줄 순서대로, 화면이 그대로 쓸 칸을 서버가 채워서 준다.

    label · legal_basis · required · sensitive 는 scope 로 정해지는 값이다. 프론트에도
    적어두면 두 곳이 어긋나므로 서버가 채운다 (#91).
    """
    body = (await db_client.get(URL)).json()

    assert [item["scope"] for item in body] == DISPLAY_ORDER
    for item in body:
        assert set(item) == {
            "scope",
            "version",
            "label",
            "legal_basis",
            "required",
            "sensitive",
            "content",
        }
        assert item["label"]
        assert item["required"] is True

    health = next(item for item in body if item["scope"] == "child_health")
    assert health["sensitive"] is True
    assert "제23조" in health["legal_basis"]


async def test_newer_version_replaces_older_one(db_client, session):
    """같은 scope 에 더 늦게 시작한 버전이 등록되면 그 버전과 본문이 나간다.

    화면은 받은 version 을 그대로 가입 요청에 싣는다 — 이 값이 가입 검사를 통과하는 값이다.
    """
    await register_version(
        session,
        scope=ConsentScope.SERVICE_TERMS,
        version="draft-1",
        content="# 새 약관",
        content_hash="hash",
        effective_at=datetime(2026, 9, 1, tzinfo=UTC),
    )

    body = (await db_client.get(URL)).json()
    terms = next(item for item in body if item["scope"] == "service_terms")

    assert terms["version"] == "draft-1"
    assert terms["content"] == "# 새 약관"
