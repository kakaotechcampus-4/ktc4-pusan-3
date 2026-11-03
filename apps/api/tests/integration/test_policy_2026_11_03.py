"""약관 2026-11-03 판 — 다섯 글을 등록한다. 모두 2026-11-03 에 시작한다.

원고는 `alembic/policy_texts/2026-11-03/<scope>.md`, 정본은 같은 폴더의 `<scope>.html` 이고
마이그레이션(c7d18f3ae5b2)이 두 파일을 그대로 넣는다. 여기서 지키는 것 여섯.
    ① 다섯이 지금 판이다. 시작 1초 전까지는 앞 판이다
    ② DB 본문 · 정본 · 제목은 저장소 파일과 한 글자도 다르지 않다
    ③ 앞 판은 이 판이 시작한 시각에 끝난 것으로 기록된다
    ④ 🚨 공개 글에 빈칸 표시 · 미정 표시가 남지 않는다. 위치 약관은 처음으로 연락처가 채워진다
    ⑤ 🚨 글로 쓴 키와 몸무게가 전송된다는 것을 건강정보 동의가 말한다
    ⑥ 🚨 문서 사진이 외부로 전송된다는 것을 아이 기본정보 동의가 말한다
"""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from app.domains.consent.models import ConsentScope
from app.domains.policy.models import PolicyVersion
from app.domains.policy.repository import find_active_versions

TEXTS = Path(__file__).resolve().parents[2] / "alembic" / "policy_texts" / "2026-11-03"

STARTS_AT = datetime(2026, 11, 2, 15, tzinfo=UTC)
"""한국 시간 2026-11-03 자정."""

PREVIOUS = {
    ConsentScope.SERVICE_TERMS: "draft-2",
    ConsentScope.PRIVACY_ACCOUNT: "draft-2",
    ConsentScope.CHILD_BASIC: "draft-2",
    ConsentScope.CHILD_HEALTH: "draft-2",
    ConsentScope.LOCATION: "draft-1",
}


async def version_row(session, scope: ConsentScope, version: str) -> PolicyVersion | None:
    return await session.scalar(
        select(PolicyVersion).where(PolicyVersion.scope == scope, PolicyVersion.version == version)
    )


async def versions_at(session, now: datetime) -> dict[ConsentScope, str]:
    rows = await find_active_versions(session, now=now)
    return {row.scope: row.version for row in rows}


@pytest.mark.parametrize("scope", PREVIOUS, ids=lambda s: s.value)
async def test_this_version_is_current(session, scope):
    """① 시작 1초 전까지는 앞 판, 그 뒤로는 이 판 — 동의 화면이 보여 주는 판이 여기서 정해진다."""
    before = await versions_at(session, STARTS_AT - timedelta(seconds=1))

    assert before[scope] == PREVIOUS[scope]
    assert (await versions_at(session, STARTS_AT))[scope] == "2026-11-03"
    assert (await versions_at(session, datetime.now(UTC)))[scope] == "2026-11-03"


@pytest.mark.parametrize("scope", PREVIOUS, ids=lambda s: s.value)
async def test_this_version_is_the_committed_text_page_and_title(session, scope):
    """② DB 본문 · 해시 · 정본 HTML · 제목 · 법적 근거 = 저장소 파일.

    등록 뒤에 파일을 고치면 여기서 깨진다. 고치려면 새 날짜 폴더와 새 마이그레이션을 만든다.
    """
    row = await version_row(session, scope, "2026-11-03")
    text = (TEXTS / f"{scope.value}.md").read_text(encoding="utf-8")
    page = (TEXTS / f"{scope.value}.html").read_text(encoding="utf-8")
    meta = json.loads((TEXTS / "meta.json").read_text(encoding="utf-8"))[scope.value]

    assert row.content == text
    assert row.content_hash == hashlib.sha256(text.encode("utf-8")).hexdigest()
    assert row.content_html == page
    assert (row.label, row.legal_basis) == (meta["label"], meta["legal_basis"])


@pytest.mark.parametrize("scope", PREVIOUS, ids=lambda s: s.value)
async def test_previous_version_is_marked_ended(session, scope):
    """③ 앞 판에 끝난 시각이 찍힌다 — 이 판이 시작한 그 시각이다."""
    this_version = await version_row(session, scope, "2026-11-03")
    previous = await version_row(session, scope, PREVIOUS[scope])

    assert this_version.effective_at == STARTS_AT
    assert previous.ended_at == this_version.effective_at


@pytest.mark.parametrize("scope", PREVIOUS, ids=lambda s: s.value)
async def test_no_blank_or_pending_marks_left(session, scope):
    """④ 🚨 공개 글에 `{{빈칸}}` · 【미정】 같은 표시가 없다."""
    content = (await version_row(session, scope, "2026-11-03")).content

    for mark in ("{{", "【", "〔", "TODO", "준비 중"):
        assert mark not in content


async def test_location_terms_have_operator_contact_and_no_age_gate(session):
    """④ 위치정보법 제19조의 연락처가 채워져 있고, 장소 조회를 월령으로 닫는다는 옛 문장이 없다."""
    content = (await version_row(session, ConsentScope.LOCATION, "2026-11-03")).content
    operator = content[content.index("### 제17조 (운영자의 연락처)") :]

    for row in ("| 주소 |", "| 전화번호 |", "| 전자우편 |"):
        assert row in operator
    assert "36개월" not in content


async def test_child_consent_tells_document_photos_are_sent(session):
    """⑥ 🚨 문서 사진은 별도 동의 없이 이 동의문으로 받는다 — 외부 전송을 여기서 말해야 한다."""
    content = (await version_row(session, ConsentScope.CHILD_BASIC, "2026-11-03")).content

    assert "문서 사진을 올리시면 사진 그대로 외부의 인공지능 회사(미국)로 전송됩니다" in content
    assert "사진을 올리지 않으면 아무것도 전송되지 않습니다" in content


async def test_health_consent_says_written_values_are_sent(session):
    """⑤ 🚨 측정 기록의 값은 보내지 않아도, 글이나 말로 남긴 키와 몸무게는 그 글과 함께 나간다."""
    content = (await version_row(session, ConsentScope.CHILD_HEALTH, "2026-11-03")).content

    assert "측정 기록에 입력하신 키와 몸무게는 보내지 않으며" in content
    assert "글로 쓰거나 말로 적으신 내용은 그 글 그대로 전송됩니다" in content
    assert "키와 몸무게는 보내지 않습니다" not in content
