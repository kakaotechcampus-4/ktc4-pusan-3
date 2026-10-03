"""보호자 초대 — docs/api/invite-v1.md §3. 지금은 코드 발행(§3-1)."""

import hashlib
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select

from app.domains.child.models import Invite, ParentChild, ParentChildRelation
from app.domains.child.repository import create_child

from .conftest import issue_bearer

ISSUE = "/api/v1/children/{cid}/invites"
ALPHABET = set("0123456789ABCDEFGHJKMNPQRSTVWXYZ")


@pytest.fixture
async def family(session):
    """owner 1명 · 같은 아이를 보는 member 1명 · 연결 없는 보호자 1명."""
    owner_headers, owner_id = await issue_bearer(session, token="owner-token")
    member_headers, member_id = await issue_bearer(session, token="member-token")
    stranger_headers, _ = await issue_bearer(session, token="stranger-token")
    child = await create_child(
        session,
        owner_parent_id=owner_id,
        nickname="별명",
        birth_date=date(2022, 3, 1),
        relation=ParentChildRelation.OTHER,
    )
    session.add(
        ParentChild(parent_id=member_id, child_id=child.id, relation=ParentChildRelation.OTHER)
    )
    await session.flush()
    return child, owner_headers, owner_id, member_headers, stranger_headers


async def test_owner_가_발행하면_해시만_저장한다(db_client, session, family):
    child, owner_headers, owner_id, _, _ = family

    res = await db_client.post(ISSUE.format(cid=child.id), json={}, headers=owner_headers)

    assert res.status_code == 201
    body = res.json()
    code = body["invite_code"]
    assert len(code) == 8 and set(code) <= ALPHABET
    expires_at = datetime.fromisoformat(body["expires_at"])
    assert timedelta(hours=23, minutes=59) < expires_at - datetime.now(UTC) <= timedelta(hours=24)

    row = await session.scalar(select(Invite).where(Invite.child_id == child.id))
    assert row.code_hash == hashlib.sha256(code.encode()).digest()
    assert row.created_by == owner_id
    assert row.used_at is None


@pytest.mark.parametrize(
    ("who", "code"), [("member", "owner_only"), ("stranger", "child_access_denied")]
)
async def test_owner_가_아니면_403(db_client, session, family, who, code):
    child, _, _, member_headers, stranger_headers = family
    headers = member_headers if who == "member" else stranger_headers

    res = await db_client.post(ISSUE.format(cid=child.id), json={}, headers=headers)

    assert res.status_code == 403
    assert res.json()["error"]["code"] == code
    assert await session.scalar(select(Invite).where(Invite.child_id == child.id)) is None
