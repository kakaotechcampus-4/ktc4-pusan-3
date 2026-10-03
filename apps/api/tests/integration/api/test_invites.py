"""보호자 초대 — docs/api/invite-v1.md §3. 지금은 코드 발행(§3-1) · 수락 전 확인(§3-2)."""

import hashlib
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select

from app.api.deps.auth import hash_token
from app.api.quota import today_kst
from app.domains.child.models import Child, Invite, ParentChild, ParentChildRelation
from app.domains.child.repository import create_child, create_invite
from app.domains.identity.models import Parent
from app.rules.age import age_display

from .conftest import issue_bearer

ISSUE = "/api/v1/children/{cid}/invites"
PREVIEW = "/api/v1/invites/{code}"
CODE = "ABCD1234"
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


async def _seed_invite(session, child, owner_id, **overrides) -> Invite:
    values = {"expires_at": datetime.now(UTC) + timedelta(hours=1)} | overrides
    invite = await create_invite(
        session,
        child_id=child.id,
        created_by=owner_id,
        code_hash=hash_token(CODE),
        expires_at=values["expires_at"],
    )
    invite.used_at = values.get("used_at")
    await session.flush()
    return invite


async def test_확인은_별명_나이만_내리고_코드를_쓰지_않는다(db_client, session, family):
    child, _, owner_id, _, stranger_headers = family
    (await session.get(Parent, owner_id)).nickname = "초대한사람"
    invite = await _seed_invite(session, child, owner_id)

    # 화면 표시 형식 · 소문자로 와도 같은 코드다
    res = await db_client.get(PREVIEW.format(code="abcd-1234"), headers=stranger_headers)

    assert res.status_code == 200
    body = res.json()
    assert body["child"] == {
        "nickname": "별명",
        "age_display": age_display(date(2022, 3, 1), today_kst()),
    }
    assert body["invited_by"] == {"nickname": "초대한사람"}
    await session.refresh(invite)
    assert invite.used_at is None


@pytest.mark.parametrize(
    ("case", "status", "code"),
    [
        ("없는 코드", 404, "invite_not_found"),
        ("짧은 코드", 404, "invite_not_found"),
        ("보관된 아이", 404, "invite_not_found"),
        ("사용됨", 409, "invite_used"),
        ("만료", 410, "invite_expired"),
    ],
)
async def test_확인_실패(db_client, session, family, case, status, code):
    child, _, owner_id, _, stranger_headers = family
    now = datetime.now(UTC)
    path_code = CODE
    if case == "없는 코드":
        path_code = "ZZZZ9999"
    elif case == "짧은 코드":
        path_code = "ABCD123"
    elif case == "보관된 아이":
        (await session.get(Child, child.id)).deleted_at = now
    await _seed_invite(
        session,
        child,
        owner_id,
        used_at=now if case == "사용됨" else None,
        expires_at=now - timedelta(seconds=1) if case == "만료" else now + timedelta(hours=1),
    )

    res = await db_client.get(PREVIEW.format(code=path_code), headers=stranger_headers)

    assert res.status_code == status
    assert res.json()["error"]["code"] == code


async def test_아이가_있는_보호자는_코드를_보기_전에_409(db_client, session, family):
    """코드가 맞든 틀리든 같은 409 다 — 코드의 유효 여부를 알려 주지 않는다."""
    child, owner_headers, owner_id, _, _ = family
    await _seed_invite(session, child, owner_id)

    for path_code in (CODE, "ZZZZ9999"):
        res = await db_client.get(PREVIEW.format(code=path_code), headers=owner_headers)
        assert res.status_code == 409
        assert res.json()["error"]["code"] == "child_already_exists"
