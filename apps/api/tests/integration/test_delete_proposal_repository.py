"""삭제 제안 저장 · 조회 — app/domains/delete_proposal/repository.py (#535).

    ① 제안한 보호자만 찾는다 — 같은 아이의 다른 보호자에게는 없는 제안이다
    ② 결정 전이라도 24시간이 지나면 만료다 (저장하지 않고 읽을 때 판정)
    ③ 끝난 제안은 다시 끝낼 수 없다 — 두 번 눌러도 한 번만 지운다
    ④ AI 가 고른 id 는 후보 안에 있어야 한다

아이 정보는 전부 가짜 값이다 (루트 CLAUDE.md §9).
"""

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest

from app.domains.child.models import Child, ParentChild, ParentChildRelation
from app.domains.delete_proposal.models import DeleteProposalStatus
from app.domains.delete_proposal.repository import (
    PROPOSAL_TTL,
    ProposalTab,
    add_proposal,
    effective_status,
    find_proposal,
    finish,
)
from app.domains.identity.models import Parent

NOW = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)


@pytest.fixture
async def family(session):
    """아이 하나에 보호자 둘(owner · member)."""
    owner, member = Parent(), Parent()
    session.add_all([owner, member])
    await session.flush()
    child = Child(owner_parent_id=owner.id, nickname="test child", birth_date=date(2023, 1, 1))
    session.add(child)
    await session.flush()
    for parent in (owner, member):
        session.add(
            ParentChild(parent_id=parent.id, child_id=child.id, relation=ParentChildRelation.OTHER)
        )
    await session.flush()
    return owner.id, member.id, child.id


def _tab(*ids: str, picked: tuple[str, ...] | None = None) -> ProposalTab:
    return ProposalTab(
        kind="observation_food", truncated=False, ids=ids, picked=ids if picked is None else picked
    )


async def _propose(session, parent_id, child_id):
    oid = str(uuid.uuid4())
    return await add_proposal(
        session,
        parent_id=parent_id,
        child_id=child_id,
        run_id=uuid.uuid4().hex,
        tabs=[_tab(oid)],
        now=NOW,
    )


async def test_only_proposer_finds_it(session, family):
    owner, member, child = family
    pid = await _propose(session, owner, child)

    assert await find_proposal(session, proposal_id=pid, parent_id=owner, child_id=child)
    assert await find_proposal(session, proposal_id=pid, parent_id=member, child_id=child) is None


async def test_pending_expires_after_ttl(session, family):
    owner, _, child = family
    pid = await _propose(session, owner, child)
    row = await find_proposal(session, proposal_id=pid, parent_id=owner, child_id=child)

    assert effective_status(row, NOW + PROPOSAL_TTL - timedelta(seconds=1)) == "pending"
    assert effective_status(row, NOW + PROPOSAL_TTL) == "expired"


async def test_finished_proposal_cannot_finish_again(session, family):
    owner, _, child = family
    pid = await _propose(session, owner, child)
    row = await find_proposal(
        session, proposal_id=pid, parent_id=owner, child_id=child, for_update=True
    )
    await finish(
        session, row, status=DeleteProposalStatus.DELETED, result={"observation_food": 1}, now=NOW
    )

    with pytest.raises(ValueError):
        await finish(session, row, status=DeleteProposalStatus.CANCELLED, result=None, now=NOW)
    # 끝난 뒤에는 만료 시각이 지나도 끝난 상태 그대로다
    assert effective_status(row, NOW + PROPOSAL_TTL * 2) == "deleted"


async def test_picked_must_be_candidate(session, family):
    owner, _, child = family
    with pytest.raises(ValueError):
        await add_proposal(
            session,
            parent_id=owner,
            child_id=child,
            run_id=uuid.uuid4().hex,
            tabs=[_tab("a", picked=("b",))],
            now=NOW,
        )
