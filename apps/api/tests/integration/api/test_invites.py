"""보호자 초대 — docs/api/invite-v1.md §3. 코드 발행(§3-1) · 수락 전 확인(§3-2) · 수락(§3-3)."""

import hashlib
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select

from app.api import invite_attempts
from app.api.deps.auth import hash_token
from app.api.quota import today_kst
from app.api.v1.routers import invites as invites_router
from app.domains.child.models import Child, Invite, ParentChild, ParentChildRelation
from app.domains.child.repository import create_child, create_invite
from app.domains.identity.models import Parent
from app.rules.age import age_display

from .conftest import issue_bearer

ISSUE = "/api/v1/children/{cid}/invites"
PREVIEW = "/api/v1/invites/{code}"
ACCEPT = "/api/v1/invites/{code}/accept"
CODE = "ABCD1234"
ALPHABET = set("0123456789ABCDEFGHJKMNPQRSTVWXYZ")


@pytest.fixture(autouse=True)
def _clear_attempts():
    """시도 카운터는 프로세스 메모리라 테스트 사이에 샌다 — 같은 IP 버킷을 함께 쓴다."""
    invite_attempts.clear()
    yield
    invite_attempts.clear()


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
        ("삭제된 아이", 404, "invite_not_found"),
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
    elif case == "삭제된 아이":
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


async def test_확인_뒤_수락하면_member_로_바로_연결된다(db_client, session, family):
    child, _, owner_id, _, stranger_headers = family
    invite = await _seed_invite(session, child, owner_id)

    assert (
        await db_client.get(PREVIEW.format(code=CODE), headers=stranger_headers)
    ).status_code == 200
    res = await db_client.post(
        ACCEPT.format(code=CODE), json={"relation": "sitter"}, headers=stranger_headers
    )

    assert res.status_code == 200
    body = res.json()
    assert body["child_id"] == str(child.id)
    assert body["role"] == "member"
    await session.refresh(invite)
    link = await session.scalar(
        select(ParentChild).where(
            ParentChild.child_id == child.id, ParentChild.parent_id == invite.used_by
        )
    )
    assert link.relation == ParentChildRelation.SITTER
    assert invite.used_at is not None


async def test_relation_없이_수락하면_other(db_client, session, family):
    child, _, owner_id, _, stranger_headers = family
    invite = await _seed_invite(session, child, owner_id)

    res = await db_client.post(ACCEPT.format(code=CODE), json={}, headers=stranger_headers)

    assert res.status_code == 200
    await session.refresh(invite)
    link = await session.scalar(select(ParentChild).where(ParentChild.parent_id == invite.used_by))
    assert link.relation == ParentChildRelation.OTHER


@pytest.mark.parametrize(
    ("case", "status", "code"),
    [("만료", 410, "invite_expired"), ("삭제된 아이", 404, "invite_not_found")],
)
async def test_수락은_만료_삭제를_다시_보고_코드를_쓰지_않는다(
    db_client, session, family, case, status, code
):
    """확인 화면과 수락 사이에 코드가 만료되거나 아이가 삭제될 수 있다 (#150 멘토 답변)."""
    child, _, owner_id, _, stranger_headers = family
    now = datetime.now(UTC)
    invite = await _seed_invite(
        session,
        child,
        owner_id,
        expires_at=now - timedelta(seconds=1) if case == "만료" else now + timedelta(hours=1),
    )
    if case == "삭제된 아이":
        (await session.get(Child, child.id)).deleted_at = now
        await session.flush()

    res = await db_client.post(ACCEPT.format(code=CODE), json={}, headers=stranger_headers)

    assert res.status_code == status
    assert res.json()["error"]["code"] == code
    await session.refresh(invite)
    assert invite.used_at is None


async def test_한_번_쓴_코드는_다른_보호자가_못_쓴다(db_client, session, family):
    child, _, owner_id, _, stranger_headers = family
    await _seed_invite(session, child, owner_id)
    other_headers, _ = await issue_bearer(session, token="other-token")

    first = await db_client.post(ACCEPT.format(code=CODE), json={}, headers=stranger_headers)
    second = await db_client.post(ACCEPT.format(code=CODE), json={}, headers=other_headers)

    assert first.status_code == 200
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "invite_used"


async def test_유니크_위반이면_연결도_소비도_남지_않는다(db_client, session, family, monkeypatch):
    """아이 보유 확인을 지난 뒤 연결하기 전에 이 보호자에게 아이가 생긴 경우.

    그 사이를 테스트에서 만들 수 없어 사전 확인을 건너뛰게 바꿔 끼운다.
    """
    child, _, owner_id, _, stranger_headers = family
    invite = await _seed_invite(session, child, owner_id)
    racer_headers, racer_id = await issue_bearer(session, token="racer-token")
    await create_child(
        session,
        owner_parent_id=racer_id,
        nickname="방금",
        birth_date=date(2023, 1, 1),
        relation=ParentChildRelation.MOTHER,
    )
    # 핸들러의 rollback 이 여기까지 되감지 않게 심은 것을 확정한다 (바깥 트랜잭션은 끝에 롤백)
    await session.commit()

    async def skip_check(*_args, **_kwargs):
        return None

    monkeypatch.setattr(invites_router, "_reject_if_has_child", skip_check)
    res = await db_client.post(ACCEPT.format(code=CODE), json={}, headers=racer_headers)

    assert res.status_code == 409
    assert res.json()["error"]["code"] == "child_already_exists"
    await session.refresh(invite)
    assert invite.used_at is None
    # 코드가 남아 있어 다른 보호자는 쓸 수 있다
    ok = await db_client.post(ACCEPT.format(code=CODE), json={}, headers=stranger_headers)
    assert ok.status_code == 200


async def test_삭제된_아이만_있어도_확인에서_409(db_client, session, family):
    """아이를 soft delete 해도 parent_child 행이 남아 수락이 막힌다.

    그러니 확인에서 아이를 보여 주기 전에 막는다.
    """
    child, _, owner_id, _, _ = family
    await _seed_invite(session, child, owner_id)
    headers, parent_id = await issue_bearer(session, token="deleted-token")
    deleted = await create_child(
        session,
        owner_parent_id=parent_id,
        nickname="삭제",
        birth_date=date(2023, 1, 1),
        relation=ParentChildRelation.MOTHER,
    )
    deleted.deleted_at = datetime.now(UTC)
    await session.flush()

    res = await db_client.get(PREVIEW.format(code=CODE), headers=headers)

    assert res.status_code == 409
    assert res.json()["error"]["code"] == "child_already_exists"


async def _fail(client, how: str, headers) -> int:
    """없는 코드로 한 번 실패한다. how 는 확인(preview) 또는 수락(accept)."""
    if how == "preview":
        res = await client.get(PREVIEW.format(code="ZZZZ9999"), headers=headers)
    else:
        res = await client.post(ACCEPT.format(code="ZZZZ9999"), json={}, headers=headers)
    return res.status_code


@pytest.mark.parametrize(
    "pattern",
    [
        ["preview"] * 6,
        ["accept"] * 6,
        ["preview", "accept", "preview", "accept", "preview", "accept"],
    ],
    ids=["확인만", "수락만", "섞어서"],
)
async def test_확인과_수락의_실패를_합쳐_5회면_6번째가_429(db_client, family, pattern):
    _, _, _, _, stranger_headers = family

    statuses = [await _fail(db_client, how, stranger_headers) for how in pattern]

    assert statuses == [404] * 5 + [429]


async def test_계정을_바꿔도_같은_IP_에서_30회를_넘으면_429(db_client, session):
    """X-Forwarded-For 를 매번 바꿔 보내도 버킷이 바뀌지 않는다 — 그 헤더를 믿지 않는다."""
    statuses = []
    for n in range(7):
        headers, _ = await issue_bearer(session, token=f"ip-token-{n}")
        for m in range(5):
            spoofed = headers | {"X-Forwarded-For": f"10.0.{n}.{m}"}
            statuses.append(await _fail(db_client, "preview", spoofed))

    assert statuses[:30] == [404] * 30
    assert statuses[30:] == [429] * 5


async def test_아이가_있는_보호자는_코드를_보기_전에_409_이고_실패로_세지_않는다(
    db_client, session, family
):
    """코드가 맞든 틀리든 같은 409 다 — 코드의 유효 여부를 알려 주지 않으니 세지도 않는다."""
    child, owner_headers, owner_id, _, _ = family
    await _seed_invite(session, child, owner_id)

    for path_code in (CODE, "ZZZZ9999") * 3:
        res = await db_client.get(PREVIEW.format(code=path_code), headers=owner_headers)
        assert res.status_code == 409
        assert res.json()["error"]["code"] == "child_already_exists"
    assert invite_attempts._failures == {}
