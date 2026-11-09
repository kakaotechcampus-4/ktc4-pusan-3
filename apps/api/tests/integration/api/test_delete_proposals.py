"""삭제 제안 확정 · 취소 — app/api/v1/routers/delete_proposals.py (#535).

    ① 후보 안의 체크한 것만 지운다 — 체크하지 않은 후보는 남고, 관찰을 지우면 Curator 를 띄운다
    ② 후보 밖 id 가 하나라도 섞이면 422 이고 아무것도 지우지 않는다
    ③ 제안한 보호자만 누른다 — 같은 아이의 다른 보호자에게는 없는 제안(404)
    ④ 만료된 제안 · 취소한 제안으로는 지울 수 없다 (409)
    ⑤ 같은 키로 다시 보내면 처음 응답을 그대로 돌려준다 — 두 번 지우지 않는다
    ⑥ run 의 카드 행 읽기가 실패해도 그 run 이 쓴 기록 · 제안은 남는다 — 읽기만 되감는다

발화 · 아이 정보는 전부 가짜 값이다 (루트 CLAUDE.md §9).
"""

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import entrypoint
from app.agents.common.datetime_rules import DateRange
from app.api import idempotency
from app.api.runs import runner
from app.api.runs.registry import RunChannel
from app.api.v1 import card_rows
from app.api.v1.routers import delete_proposals
from app.core import llm_usage
from app.domains.delete_proposal.models import DeleteProposal
from app.domains.delete_proposal.repository import PROPOSAL_TTL, ProposalTab, add_proposal
from app.domains.memory.observation.models import ObservationFood, ObservationStatus
from app.domains.memory.profile.models import MemoryDomain, ProfileAffinity, ProfileState
from app.domains.memory.store.db_store import DbMemoryStore
from app.domains.schedule.models import Event, EventCategory, EventCreatedBy, EventType
from app.domains.schedule.repository import create_event
from tests.integration.api.conftest import Bearer, issue_bearer, link_child


@pytest.fixture(autouse=True)
def _clean_keys():
    idempotency.clear()
    yield
    idempotency.clear()


@pytest.fixture
def curator_attributions() -> list:
    """Curator 를 띄우는 순간 걸려 있던 LLM 사용량 귀속 (#710)."""
    return []


@pytest.fixture
def curator_calls(monkeypatch, curator_attributions):
    calls: list[uuid.UUID] = []

    def trigger(child_id, today):
        calls.append(child_id)
        curator_attributions.append(llm_usage.current())

    monkeypatch.setattr(runner, "curator_trigger", lambda: trigger)
    return calls


async def _meal(session: AsyncSession, cid: uuid.UUID, pid: uuid.UUID, subject: str) -> str:
    store = DbMemoryStore(session, child_id=cid)
    row = await store.create_observation(
        domain="food",
        child_id=cid,
        source_writer=pid,
        raw_text=f"synthetic {subject}",
        observed_on=date(2026, 10, 27),
        observed_range=DateRange(start=date(2026, 10, 27), end=date(2026, 10, 28)),
        fields={"subject": subject, "polarity": 1, "confidence_source": "parent_direct"},
    )
    return row.id


async def _event(session: AsyncSession, cid: uuid.UUID) -> str:
    event = await create_event(
        session,
        child_id=cid,
        title="synthetic event",
        event_type=EventType.EPISODIC,
        starts_at=datetime(2026, 10, 28, 1, tzinfo=UTC),
        ends_at=None,
        all_day=False,
        category=EventCategory.ACTIVITY,
        created_by=EventCreatedBy.CAREGIVER,
    )
    return str(event.id)


async def _propose(session, cid, pid, tabs, *, now: datetime | None = None) -> uuid.UUID:
    return await add_proposal(
        session,
        parent_id=pid,
        child_id=cid,
        run_id=uuid.uuid4().hex,
        tabs=tabs,
        now=now or datetime.now(UTC),
    )


def _url(cid, proposal_id, action="confirm") -> str:
    return f"/api/v1/children/{cid}/delete-proposals/{proposal_id}/{action}"


def _key(headers: dict, key: str | None = None) -> dict:
    return {**headers, "Idempotency-Key": key or uuid.uuid4().hex}


async def _status(session: AsyncSession, oid: str) -> ObservationStatus:
    row = await session.get(ObservationFood, uuid.UUID(oid), populate_existing=True)
    return row.status


async def test_deletes_only_checked_candidates(
    db_client: AsyncClient,
    session: AsyncSession,
    bearer: Bearer,
    cid: uuid.UUID,
    curator_calls,
    curator_attributions,
):
    headers, pid = bearer
    lunch, snack = await _meal(session, cid, pid, "rice"), await _meal(session, cid, pid, "snack")
    event = await _event(session, cid)
    proposal = await _propose(
        session,
        cid,
        pid,
        [
            ProposalTab("observation_food", False, (lunch, snack), (lunch,)),
            ProposalTab("event", False, (event,), (event,)),
        ],
    )

    resp = await db_client.post(
        _url(cid, proposal), json={"ids": [lunch, event]}, headers=_key(headers)
    )

    assert resp.status_code == 200
    body = resp.json()["proposal"]
    assert body["status"] == "deleted"
    assert body["result"] == {"observation_food": 1, "event": 1}
    assert await _status(session, lunch) is ObservationStatus.DELETED
    assert await _status(session, snack) is not ObservationStatus.DELETED
    assert await session.scalar(select(Event.id).where(Event.id == uuid.UUID(event))) is None
    assert curator_calls == [cid]
    # 삭제 확정은 run 밖에서 Curator 를 띄운다 — 확정한 보호자로 귀속된다 (#710)
    [attribution] = curator_attributions
    assert (attribution.parent_id, attribution.source) == (pid, "delete")


async def test_outside_id_deletes_nothing(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    headers, pid = bearer
    lunch, other = await _meal(session, cid, pid, "rice"), await _meal(session, cid, pid, "soup")
    proposal = await _propose(
        session, cid, pid, [ProposalTab("observation_food", False, (lunch,), (lunch,))]
    )

    resp = await db_client.post(
        _url(cid, proposal), json={"ids": [lunch, other]}, headers=_key(headers)
    )

    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "not_in_proposal"
    assert await _status(session, lunch) is not ObservationStatus.DELETED
    assert await _status(session, other) is not ObservationStatus.DELETED


async def test_other_guardian_cannot_press(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    _, owner = bearer
    member_headers, member = await issue_bearer(session, token="member")
    await link_child(session, parent_id=member, child_id=cid)
    lunch = await _meal(session, cid, owner, "rice")
    proposal = await _propose(
        session, cid, owner, [ProposalTab("observation_food", False, (lunch,), (lunch,))]
    )

    resp = await db_client.post(
        _url(cid, proposal), json={"ids": [lunch]}, headers=_key(member_headers)
    )

    assert resp.status_code == 404
    assert await _status(session, lunch) is not ObservationStatus.DELETED


async def test_expired_or_cancelled_cannot_delete(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    headers, pid = bearer
    lunch = await _meal(session, cid, pid, "rice")
    tab = [ProposalTab("observation_food", False, (lunch,), (lunch,))]
    expired = await _propose(
        session, cid, pid, tab, now=datetime.now(UTC) - PROPOSAL_TTL - timedelta(minutes=1)
    )
    cancelled = await _propose(session, cid, pid, tab)
    cancel = await db_client.post(_url(cid, cancelled, "cancel"), headers=headers)
    assert cancel.json()["proposal"]["status"] == "cancelled"

    late = await db_client.post(_url(cid, expired), json={"ids": [lunch]}, headers=_key(headers))
    closed = await db_client.post(
        _url(cid, cancelled), json={"ids": [lunch]}, headers=_key(headers)
    )

    assert (late.status_code, late.json()["error"]["code"]) == (409, "proposal_expired")
    assert (closed.status_code, closed.json()["error"]["code"]) == (409, "proposal_closed")
    assert await _status(session, lunch) is not ObservationStatus.DELETED


async def test_same_key_replays_first_response(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    headers, pid = bearer
    lunch = await _meal(session, cid, pid, "rice")
    proposal = await _propose(
        session, cid, pid, [ProposalTab("observation_food", False, (lunch,), (lunch,))]
    )
    keyed = _key(headers, "same-key")

    first = await db_client.post(_url(cid, proposal), json={"ids": [lunch]}, headers=keyed)
    again = await db_client.post(_url(cid, proposal), json={"ids": [lunch]}, headers=keyed)

    assert first.status_code == again.status_code == 200
    assert again.json() == first.json()
    row = await session.get(DeleteProposal, proposal, populate_existing=True)
    assert row.result == {"observation_food": 1}


async def test_expiring_while_waiting_for_the_lock_cannot_delete(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID, monkeypatch
):
    """잠금을 기다리는 사이 만료되면 지우지 않는다 — 시각은 잠금을 얻은 뒤에 잰다.

    잠금 대기를 흉내 낸다: 잠금을 얻는 순간 시계를 만료 뒤로 옮긴다. 기다리기 전 시각으로 보면
    200 이 나고 지워진다.
    """
    headers, pid = bearer
    lunch = await _meal(session, cid, pid, "rice")
    created = datetime.now(UTC)
    proposal = await _propose(
        session,
        cid,
        pid,
        [ProposalTab("observation_food", False, (lunch,), (lunch,))],
        now=created,
    )
    clock = [created]

    class _Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0]

    locked = delete_proposals._locked

    async def slow_lock(*args, **kwargs):
        row = await locked(*args, **kwargs)
        clock[0] = created + PROPOSAL_TTL + timedelta(seconds=1)
        return row

    monkeypatch.setattr(delete_proposals, "datetime", _Clock)
    monkeypatch.setattr(delete_proposals, "_locked", slow_lock)

    resp = await db_client.post(_url(cid, proposal), json={"ids": [lunch]}, headers=_key(headers))

    assert (resp.status_code, resp.json()["error"]["code"]) == (409, "proposal_expired")
    assert await _status(session, lunch) is not ObservationStatus.DELETED


async def test_failed_card_read_keeps_what_the_run_wrote(
    session: AsyncSession, bearer: Bearer, cid: uuid.UUID, monkeypatch
):
    """카드 읽기가 DB 오류로 실패해도 같은 run 세션의 아직 commit 하지 않은 기록 · 제안은
    그대로다. run 세션을 통째로 되감으면 run 은 성공으로 끝나는데 기록이 사라지고, 카드의
    [삭제] 는 404 가 된다. 부르는 순서(Memory commit 뒤)에 기대지 않고 읽기만 SAVEPOINT 로
    되감는지 본다."""
    _, pid = bearer
    meal = await _meal(session, cid, pid, "rice")
    proposal = await _propose(
        session, cid, pid, [ProposalTab("observation_food", False, (meal,), (meal,))]
    )

    async def broken_read(db, **_kwargs):
        await db.execute(text("SELECT 1 / 0"))

    monkeypatch.setattr(card_rows, "read_card_rows", broken_read)
    refs = (entrypoint.Ref("observation_food", uuid.UUID(meal)),)

    rows = await runner._read_cards(session, cid, pid, refs, RunChannel("run-card-read", pid))

    assert rows == {}
    assert await session.get(ObservationFood, uuid.UUID(meal), populate_existing=True) is not None
    assert await session.get(DeleteProposal, proposal, populate_existing=True) is not None


async def test_deleting_observation_recomputes_its_memory(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID, curator_calls
):
    """지운 관찰 몫을 기억에서 다시 계산한다 — store 는 flush_profiles 에서 계산한다 (#717)."""
    headers, pid = bearer
    earlier = date(2026, 10, 20)
    latest = await _meal(session, cid, pid, "rice")  # 10-27
    kept = await DbMemoryStore(session, child_id=cid).create_observation(
        domain="food",
        child_id=cid,
        source_writer=pid,
        raw_text="synthetic rice",
        observed_on=earlier,
        observed_range=DateRange(start=earlier, end=earlier + timedelta(days=1)),
        fields={"subject": "rice", "polarity": 1, "confidence_source": "parent_direct"},
    )
    profile = ProfileAffinity(
        child_id=cid,
        domain=MemoryDomain.FOOD,
        polarity=1,
        merge_key="rice",
        state=ProfileState.CANDIDATE,
        last_observed_on=date(2026, 10, 27),
    )
    session.add(profile)
    await session.flush()
    await session.execute(
        update(ObservationFood)
        .where(ObservationFood.id.in_([uuid.UUID(latest), uuid.UUID(kept.id)]))
        .values(affinity_id=profile.id)
    )
    proposal = await _propose(
        session, cid, pid, [ProposalTab("observation_food", False, (latest,), (latest,))]
    )

    resp = await db_client.post(_url(cid, proposal), json={"ids": [latest]}, headers=_key(headers))

    assert resp.status_code == 200
    await session.refresh(profile)
    assert profile.last_observed_on == earlier
