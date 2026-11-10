"""약관 2026-11-10 판 — 네 글을 등록한다. 모두 2026-11-10 에 시작한다.

원고는 `alembic/policy_texts/2026-11-10/<scope>.md`, 정본은 같은 폴더의 `<scope>.html` 이고
마이그레이션(d41e7a0c9b26)이 두 파일을 그대로 넣는다. 여기서 지키는 것 다섯.
    ① 넷이 지금 판이다. 시작 1초 전까지는 2026-11-03 판이다.
       보호자 개인정보는 2026-11-03 판 그대로다
    ② DB 본문 · 정본 · 제목은 저장소 파일과 한 글자도 다르지 않다
    ③ 앞 판은 이 판이 시작한 시각에 끝난 것으로 기록된다
    ④ 🚨 공개 글에 빈칸 표시 · 미정 표시가 남지 않는다
    ⑤ 🚨 만들지 않기로 한 기능(활동 사진 · 병원 찾기)의 문장이 남지 않는다
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

VERSION = "2026-11-10"
TEXTS = Path(__file__).resolve().parents[2] / "alembic" / "policy_texts" / VERSION

STARTS_AT = datetime(2026, 11, 9, 15, tzinfo=UTC)
"""한국 시간 2026-11-10 자정."""

PREVIOUS = {
    ConsentScope.SERVICE_TERMS: "2026-11-03",
    ConsentScope.CHILD_BASIC: "2026-11-03",
    ConsentScope.CHILD_HEALTH: "2026-11-03",
    ConsentScope.LOCATION: "2026-11-03",
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
    assert (await versions_at(session, STARTS_AT))[scope] == VERSION
    assert (await versions_at(session, datetime.now(UTC)))[scope] == VERSION


async def test_privacy_account_stays_on_2026_11_03(session):
    """① 보호자 개인정보 동의문은 바뀐 글이 없어 이 판에 없다.

    2026-11-03 판이 그대로 지금 판이다.
    """
    assert await version_row(session, ConsentScope.PRIVACY_ACCOUNT, VERSION) is None
    current = await versions_at(session, datetime.now(UTC))
    assert current[ConsentScope.PRIVACY_ACCOUNT] == "2026-11-03"


@pytest.mark.parametrize("scope", PREVIOUS, ids=lambda s: s.value)
async def test_this_version_is_the_committed_text_page_and_title(session, scope):
    """② DB 본문 · 해시 · 정본 HTML · 제목 · 법적 근거 = 저장소 파일.

    등록 뒤에 파일을 고치면 여기서 깨진다. 고치려면 새 날짜 폴더와 새 마이그레이션을 만든다.
    """
    row = await version_row(session, scope, VERSION)
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
    this_version = await version_row(session, scope, VERSION)
    previous = await version_row(session, scope, PREVIOUS[scope])

    assert this_version.effective_at == STARTS_AT
    assert previous.ended_at == this_version.effective_at


@pytest.mark.parametrize("scope", PREVIOUS, ids=lambda s: s.value)
async def test_no_blank_or_pending_marks_left(session, scope):
    """④ 🚨 공개 글에 `{{빈칸}}` · 【미정】 같은 표시가 없다."""
    content = (await version_row(session, scope, VERSION)).content

    for mark in ("{{", "【", "〔", "TODO", "준비 중", "기능이 열린 뒤"):
        assert mark not in content


@pytest.mark.parametrize("scope", PREVIOUS, ids=lambda s: s.value)
async def test_dropped_features_are_gone(session, scope):
    """⑤ 🚨 활동 사진(만들지 않기로 함)과 병원 찾기(실행 경로 없음)를 말하지 않는다.

    부칙이 "무엇을 뺐는지" 를 말하는 한 줄은 남는다 — 그 줄은 빼고 본다.
    """
    content = (await version_row(session, scope, VERSION)).content
    body = content.split("## 부칙")[0].split("### 부칙")[0]

    assert "활동 사진" not in body
    assert "병원 찾기" not in body
    assert "국립중앙의료원" not in content
