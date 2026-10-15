"""약관 draft-2 — 서비스 이용약관 · 보호자 개인정보 · 아이 동의 둘을 등록한다 (#221).

원고는 `alembic/policy_texts/draft-2/<scope>.md`, 정본은 같은 폴더의 `<scope>.html` 이고
마이그레이션(b5be2945826e)이 두 파일을 그대로 넣는다. 여기서 지키는 것 넷.
    ① 넷이 지금 판이다. 위치는 이어받지 않아 draft-1 이 그대로다
    ② DB 본문 · 정본 · 제목은 저장소 파일과 한 글자도 다르지 않다
    ③ 앞 판(draft-1 · draft-0)은 draft-2 가 시작한 시각에 끝난 것으로 기록된다
    ④ 🚨 공개 글에 빈칸 표시 · 미정 표시가 남지 않는다. 아이 동의 둘은 처음으로 진짜 본문이다
"""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import select

from app.domains.consent.models import ConsentScope
from app.domains.policy.models import PolicyVersion
from app.domains.policy.repository import find_active_versions

TEXTS = Path(__file__).resolve().parents[2] / "alembic" / "policy_texts" / "draft-2"
REGISTERED = (
    ConsentScope.SERVICE_TERMS,
    ConsentScope.PRIVACY_ACCOUNT,
    ConsentScope.CHILD_BASIC,
    ConsentScope.CHILD_HEALTH,
)
PREVIOUS = {
    ConsentScope.SERVICE_TERMS: "draft-1",
    ConsentScope.PRIVACY_ACCOUNT: "draft-1",
    ConsentScope.CHILD_BASIC: "draft-0",
    ConsentScope.CHILD_HEALTH: "draft-0",
}


async def version_row(session, scope: ConsentScope, version: str) -> PolicyVersion | None:
    return await session.scalar(
        select(PolicyVersion).where(PolicyVersion.scope == scope, PolicyVersion.version == version)
    )


async def current_versions(session) -> dict[ConsentScope, str]:
    rows = await find_active_versions(session, now=datetime.now(UTC))
    return {row.scope: row.version for row in rows}


@pytest.mark.parametrize("scope", REGISTERED, ids=lambda s: s.value)
async def test_draft_2_is_the_current_version(session, scope):
    """① 동의 화면(GET /policies)이 지금 보여 주는 버전이 draft-2 다."""
    assert (await current_versions(session))[scope] == "draft-2"


async def test_location_stays_on_draft_1(session):
    """① 위치는 이어받지 않는다 — 첫 빌드에서 화면이 묻지 않고, draft-1 이 그대로 유효하다."""
    assert (await current_versions(session))[ConsentScope.LOCATION] == "draft-1"
    assert await version_row(session, ConsentScope.LOCATION, "draft-2") is None


@pytest.mark.parametrize("scope", REGISTERED, ids=lambda s: s.value)
async def test_draft_2_is_the_committed_text_page_and_title(session, scope):
    """② DB 본문 · 해시 · 정본 HTML · 제목 · 법적 근거 = 저장소 파일.

    등록 뒤에 파일을 고치면 여기서 깨진다. 고치려면 draft-3 폴더와 새 마이그레이션을 만든다.
    """
    row = await version_row(session, scope, "draft-2")
    text = (TEXTS / f"{scope.value}.md").read_text(encoding="utf-8")
    page = (TEXTS / f"{scope.value}.html").read_text(encoding="utf-8")
    meta = json.loads((TEXTS / "meta.json").read_text(encoding="utf-8"))[scope.value]

    assert row.content == text
    assert row.content_hash == hashlib.sha256(text.encode("utf-8")).hexdigest()
    assert row.content_html == page
    assert (row.label, row.legal_basis) == (meta["label"], meta["legal_basis"])


@pytest.mark.parametrize("scope", REGISTERED, ids=lambda s: s.value)
async def test_previous_version_is_marked_ended(session, scope):
    """③ 앞 판에 끝난 시각이 찍힌다 — draft-2 가 시작한 그 시각이다."""
    draft_2 = await version_row(session, scope, "draft-2")
    previous = await version_row(session, scope, PREVIOUS[scope])

    assert previous.ended_at == draft_2.effective_at


@pytest.mark.parametrize("scope", REGISTERED, ids=lambda s: s.value)
async def test_no_blank_or_pending_marks_left(session, scope):
    """④ 🚨 공개 글에 `{{빈칸}}` · 【미정】 같은 표시가 없다. 아이 동의는 "TODO" 가 아니다."""
    content = (await version_row(session, scope, "draft-2")).content

    for mark in ("{{", "【", "〔", "TODO"):
        assert mark not in content


async def test_operator_contact_is_written_in(session):
    """④ 운영자 정보의 주소 · 전자우편 · 전화번호가 채워져 있다 (2026-10-15 PM — 원고에 직접)."""
    content = (await version_row(session, ConsentScope.SERVICE_TERMS, "draft-2")).content
    operator = content[content.index("## 운영자 정보") :]

    for row in ("| 주소 |", "| 전자우편 |", "| 전화번호 |"):
        assert row in operator
    assert "준비 중" not in operator
