"""약관 초안 draft-1 — 보호자가 실제로 읽을 본문을 등록한다 (#172).

원고는 `alembic/policy_texts/draft-1/<scope>.md` 이고 마이그레이션이 그 글을 그대로 넣는다.
여기서 지키는 것 셋.
    ① DB 본문은 저장소 원고와 한 글자도 다르지 않다 — 원문·해시가 같은 글 한 벌에서 나온다
    ② 새 버전을 등록한 scope 의 draft-0 은 끝난 것으로 기록된다. 개정 중인 아이 동의 둘은 그대로다
    ③ 🚨 운영자 주소·전화는 빈칸 그대로다 — 저장소가 public 이라 실제 값은 배포 때만 채운다 (#166)
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

TEXTS = Path(__file__).resolve().parents[2] / "alembic" / "policy_texts" / "draft-1"
REGISTERED = (ConsentScope.SERVICE_TERMS, ConsentScope.PRIVACY_ACCOUNT, ConsentScope.LOCATION)
STILL_DRAFT_0 = (ConsentScope.CHILD_BASIC, ConsentScope.CHILD_HEALTH)


async def version_row(session, scope: ConsentScope, version: str) -> PolicyVersion | None:
    return await session.scalar(
        select(PolicyVersion).where(PolicyVersion.scope == scope, PolicyVersion.version == version)
    )


@pytest.mark.parametrize("scope", REGISTERED, ids=lambda s: s.value)
async def test_draft_1_is_the_current_version(session, scope):
    """동의 화면(GET /policies)이 지금 보여 주는 버전이 draft-1 이다."""
    current = {row.scope: row for row in await find_active_versions(session, now=datetime.now(UTC))}

    assert current[scope].version == "draft-1"


@pytest.mark.parametrize("scope", REGISTERED, ids=lambda s: s.value)
async def test_draft_1_content_is_the_committed_text(session, scope):
    """① DB 본문 = 저장소 원고. 해시도 그 본문의 해시다.

    원고를 등록 뒤에 고치면 여기서 깨진다. 고치려면 파일을 덮어쓰지 말고 새 버전
    (draft-2) 폴더와 마이그레이션을 만든다 — 이미 누군가 동의한 글이 바뀌면 안 된다.
    """
    row = await version_row(session, scope, "draft-1")
    text = (TEXTS / f"{scope.value}.md").read_text(encoding="utf-8")

    assert row.content == text
    assert row.content_hash == hashlib.sha256(text.encode("utf-8")).hexdigest()


@pytest.mark.parametrize("scope", REGISTERED, ids=lambda s: s.value)
async def test_draft_1_title_is_the_committed_meta(session, scope):
    """제목 · 법적 근거도 저장소의 meta.json 그대로다 — 보호자가 체크박스 옆에서 본 글이다."""
    row = await version_row(session, scope, "draft-1")
    meta = json.loads((TEXTS / "meta.json").read_text(encoding="utf-8"))[scope.value]

    assert (row.label, row.legal_basis) == (meta["label"], meta["legal_basis"])


@pytest.mark.parametrize("scope", REGISTERED, ids=lambda s: s.value)
async def test_superseded_draft_0_is_marked_ended(session, scope):
    """② 옛 버전에 끝난 시각이 찍힌다 — 새 버전이 시작한 그 시각이다."""
    draft_1 = await version_row(session, scope, "draft-1")
    draft_0 = await version_row(session, scope, "draft-0")

    if draft_0 is not None:  # location 은 draft-0 이 없던 scope 다
        assert draft_0.ended_at == draft_1.effective_at


@pytest.mark.parametrize("scope", STILL_DRAFT_0, ids=lambda s: s.value)
async def test_child_consents_stay_on_draft_0(session, scope):
    """② 동의문 2·3 은 개정 중이라 이번에 올리지 않는다. draft-0 이 그대로 유효하다."""
    draft_0 = await version_row(session, scope, "draft-0")

    assert draft_0.ended_at is None
    assert await version_row(session, scope, "draft-1") is None


@pytest.mark.parametrize(
    ("scope", "blanks"),
    [
        (ConsentScope.SERVICE_TERMS, ("{{운영자_주소}}",)),
        (ConsentScope.LOCATION, ("{{운영자_주소}}", "{{운영자_전화}}")),
    ],
    ids=lambda v: v.value if isinstance(v, ConsentScope) else "",
)
async def test_operator_contact_stays_blank(session, scope, blanks):
    """③ 🚨 주소·전화는 빈칸 표시 그대로 등록한다. 실제 값이 원고에 들어오면 여기서 깨진다."""
    row = await version_row(session, scope, "draft-1")

    for blank in blanks:
        assert blank in row.content


async def test_location_is_one_text_with_summary_first(session):
    """위치는 체크박스 하나에 글 하나 (#172 결정 a). 쉬운 요약이 먼저, 약관 전문이 아래다."""
    content = (await version_row(session, ConsentScope.LOCATION, "draft-1")).content

    summary = content.index("## 꼭 알려야 하는 것")
    terms = content.index("## 위치기반서비스 이용약관")
    assert summary < terms
    assert "### 제17조 (운영자의 연락처)" in content[terms:]
