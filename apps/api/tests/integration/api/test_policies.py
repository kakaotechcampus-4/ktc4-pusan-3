"""GET /api/v1/policies — 동의 화면이 그릴 약관을 서버가 내려준다 (#91).

왜 필요한가 — 동의 화면이 정책 버전을 프론트 상수로 들고 있으면 DB 에 등록된 버전과
어긋난다. 실제로 화면은 `2026-09-01` 을 보내고 DB 에는 `draft-0` 뿐이라 신규 가입이
전부 `400 policy_version_invalid` 로 막혔다 (멘토 #71 P1). 화면이 버전과 본문을 여기서
받아 그대로 쓰면 둘이 어긋날 수 없다.

🚨 로그인 없이 부른다. 동의 화면은 계정이 만들어지기 전에 뜬다 (루트 CLAUDE.md §9).
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.domains.consent.models import ConsentScope
from app.domains.policy.models import PolicyVersion
from app.domains.policy.repository import register_version

URL = "/api/v1/policies"
DISPLAY_ORDER = ["service_terms", "privacy_account", "child_basic", "child_health"]


async def test_policies_are_readable_without_login(db_client):
    """Authorization 헤더 없이 200. 가입 전 동의 화면이 부른다."""
    response = await db_client.get(URL)

    assert response.status_code == 200


async def test_policies_come_in_display_order_with_labels(db_client):
    """화면에 보여줄 순서대로, 화면이 그대로 쓸 칸을 서버가 채워서 준다.

    required · sensitive 는 서버가 지키는 규칙(catalog.py), label · legal_basis 는 그 버전의
    글(policy_version)이다. 프론트에도 적어두면 두 곳이 어긋나므로 서버가 채운다 (#91 · #168 리뷰).
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
        label="서비스 이용약관",
        content="# 새 약관",
        content_hash="hash",
        effective_at=datetime(2026, 9, 1, tzinfo=UTC),
    )

    body = (await db_client.get(URL)).json()
    terms = next(item for item in body if item["scope"] == "service_terms")

    assert terms["version"] == "draft-1"
    assert terms["content"] == "# 새 약관"


async def test_title_belongs_to_the_version_the_guardian_saw(db_client, session):
    """제목 · 법적 근거는 버전마다 저장된다 — 새 버전에서 제목을 바꿔도 옛 버전의 제목은 그대로다.

    예전에는 코드(catalog.py) 한 곳에 있어서, 제목을 바꾸면 옛 동의 기록에도 새 제목이
    소급 적용됐다. 보호자가 체크박스 옆에서 본 제목은 그 버전의 것이어야 한다 (#168 리뷰).
    """
    old = await session.scalar(
        select(PolicyVersion).where(
            PolicyVersion.scope == ConsentScope.CHILD_HEALTH, PolicyVersion.version == "draft-0"
        )
    )
    await register_version(
        session,
        scope=ConsentScope.CHILD_HEALTH,
        version="test-renamed",
        label="민감정보 처리 (아이 건강·알레르기·새 항목)",
        legal_basis="개인정보보호법 제23조",
        content="# 새 약관",
        content_hash="hash",
        # "방금" — 고정 날짜는 그보다 늦은 약관이 등록되는 날 가장 새 버전이 아니게 된다.
        effective_at=datetime.now(UTC) - timedelta(minutes=1),
    )

    body = (await db_client.get(URL)).json()
    health = next(item for item in body if item["scope"] == "child_health")

    assert health["label"] == "민감정보 처리 (아이 건강·알레르기·새 항목)"
    assert health["legal_basis"] == "개인정보보호법 제23조"
    await session.refresh(old)
    assert old.label == "민감정보 처리 (아이 건강·알레르기)", "옛 버전의 제목은 바뀌지 않는다"
