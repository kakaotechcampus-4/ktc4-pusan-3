"""기록 삭제 API — `DELETE /children/{cid}/observations/{kind}/{oid}` (#736).

    ① deleted 가 아닌 기록은 상태와 상관없이 지운다 — 고친 기록(stand_alone)도
    ② owner · member 모두 지운다. 연결되지 않은 보호자는 403
    ③ 없는 종류 · 없는 기록 · 이미 지운 기록 · 다른 아이의 기록은 404, 키가 없으면 400
    ④ 같은 키로 다시 오면 처음처럼 204 — 두 번 지우지 않는다. 잠금에서 기다린 사이 앞의 것이
       끝났어도 204 다
    ⑤ 지운 건강 기록은 증상 반복 안내의 날 수에서 빠진다

store 삭제 · #712 확정 API 와 결과가 같은지는
`tests/integration/test_observation_delete_paths.py` 가 본다.
발화 · 아이 정보는 전부 가짜 값이다 (루트 CLAUDE.md §9).
"""

import asyncio
import uuid
from datetime import timedelta
from zoneinfo import ZoneInfo

import pytest
from httpx import AsyncClient
from sqlalchemy import delete, update
from sqlalchemy.dialects.postgresql import Range
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agents.health.hook import symptom_days
from app.agents.memory_bridge import StoreHealthMemory
from app.api import idempotency
from app.api.quota import today_kst
from app.api.runs import runner
from app.api.v1.routers import observations
from app.core import llm_usage
from app.domains.child.models import Child
from app.domains.identity.models import Parent
from app.domains.memory.observation.models import (
    ConfidenceSource,
    ObservationFood,
    ObservationStatus,
    RoutineCategory,
)
from app.domains.memory.observation.repository import create_observation
from app.domains.memory.store.db_store import DbMemoryStore
from app.infra.db.session import engine
from tests.integration.api.conftest import (
    Bearer,
    backend_pid,
    issue_bearer,
    link_child,
    seed_login,
    wait_for_lock_wait,
)

_FIELDS = {
    "food": {"subject": "사과", "action": "먹음"},
    "education": {"subject": "그림책", "topic": "그림책"},
    "activity": {"subject": "블록", "activity": "블록 쌓기"},
    "routine": {"subject": "양치", "routine_category": RoutineCategory.SELF_CARE},
    "health": {"symptom": ["기침"]},
}


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


async def _observe(
    session: AsyncSession, cid: uuid.UUID, writer: uuid.UUID, domain: str, days_ago: int = 0
):
    day = today_kst() - timedelta(days=days_ago)
    return await create_observation(
        session,
        domain=domain,
        child_id=cid,
        source_writer=writer,
        raw_text=f"synthetic {domain}",
        observed_range=Range(day, day + timedelta(days=1), bounds="[)"),
        fields={"confidence_source": ConfidenceSource.PARENT_DIRECT, **_FIELDS[domain]},
    )


def _url(cid, oid, kind: str = "observation_food") -> str:
    return f"/api/v1/children/{cid}/observations/{kind}/{oid}"


def _key(headers: dict, key: str | None = None) -> dict:
    return {**headers, "Idempotency-Key": key or uuid.uuid4().hex}


async def _status(session: AsyncSession, model: type, oid: uuid.UUID) -> ObservationStatus:
    row = await session.get(model, oid, populate_existing=True)
    return row.status


@pytest.mark.parametrize(
    "status",
    [ObservationStatus.ACTIVE, ObservationStatus.STAND_ALONE],
)
async def test_deletes_whatever_the_status_and_starts_curator(
    db_client: AsyncClient,
    session: AsyncSession,
    bearer: Bearer,
    cid: uuid.UUID,
    curator_calls,
    curator_attributions,
    status: ObservationStatus,
):
    headers, pid = bearer
    record = await _observe(session, cid, pid, "food")
    await session.execute(
        update(ObservationFood).where(ObservationFood.id == record.id).values(status=status)
    )

    resp = await db_client.delete(_url(cid, record.id), headers=_key(headers))

    assert resp.status_code == 204
    assert resp.content == b""
    assert await _status(session, ObservationFood, record.id) is ObservationStatus.DELETED
    assert curator_calls == [cid]
    # 기록 삭제는 run 밖에서 Curator 를 띄운다 — 지운 보호자로 귀속된다 (#710)
    [attribution] = curator_attributions
    assert (attribution.parent_id, attribution.source) == (pid, "delete")


async def test_member_can_delete_and_unlinked_guardian_cannot(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID, curator_calls
):
    _, owner = bearer
    member_headers, member = await issue_bearer(session, token="member")
    await link_child(session, parent_id=member, child_id=cid)
    stranger_headers, _ = await issue_bearer(session, token="stranger")
    by_member = await _observe(session, cid, owner, "food")
    untouched = await _observe(session, cid, owner, "food")

    member_resp = await db_client.delete(_url(cid, by_member.id), headers=_key(member_headers))
    stranger_resp = await db_client.delete(_url(cid, untouched.id), headers=_key(stranger_headers))

    assert member_resp.status_code == 204
    assert (stranger_resp.status_code, stranger_resp.json()["error"]["code"]) == (
        403,
        "child_access_denied",
    )
    assert await _status(session, ObservationFood, untouched.id) is ObservationStatus.ACTIVE


async def test_missing_key_unknown_kind_gone_or_other_childs_record(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID, curator_calls
):
    headers, pid = bearer
    _, stranger = await issue_bearer(session, token="stranger")
    other_cid = await link_child(session, parent_id=stranger)
    record = await _observe(session, cid, pid, "food")
    others = await _observe(session, other_cid, stranger, "food")
    gone = await _observe(session, cid, pid, "food")
    assert await DbMemoryStore(session, child_id=cid).delete_observation(
        domain="food", observation_id=str(gone.id)
    )

    no_key = await db_client.delete(_url(cid, record.id), headers=headers)
    cases = {
        "unknown kind": _url(cid, record.id, "observation_sleep"),
        "not a table": _url(cid, record.id, "event"),
        "no such record": _url(cid, uuid.uuid4()),
        "already deleted": _url(cid, gone.id),
        "other child's record": _url(cid, others.id),
        "right table, wrong kind": _url(cid, record.id, "observation_activity"),
    }
    results = {
        name: (await db_client.delete(url, headers=_key(headers))) for name, url in cases.items()
    }

    assert (no_key.status_code, no_key.json()["error"]["code"]) == (
        400,
        "idempotency_key_required",
    )
    assert {name: r.status_code for name, r in results.items()} == dict.fromkeys(cases, 404)
    assert await _status(session, ObservationFood, record.id) is ObservationStatus.ACTIVE
    assert await _status(session, ObservationFood, others.id) is ObservationStatus.ACTIVE
    assert curator_calls == []


async def test_same_key_twice_is_204_both_times_and_deletes_once(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID, curator_calls
):
    headers, pid = bearer
    record = await _observe(session, cid, pid, "food")
    keyed = _key(headers, "same-key")

    first = await db_client.delete(_url(cid, record.id), headers=keyed)
    again = await db_client.delete(_url(cid, record.id), headers=keyed)
    fresh = await db_client.delete(_url(cid, record.id), headers=_key(headers))

    assert (first.status_code, again.status_code) == (204, 204)
    assert fresh.status_code == 404  # 다른 키면 이미 지운 기록이다
    assert curator_calls == [cid]


# ── 같은 키가 겹칠 때 (#774) ────────────────────────────────────────────────────
# 두 요청이 실제로 잠금에서 겹쳐야 해서 한 트랜잭션 안의 `session` 으로는 재현할 수 없다. 커밋하는
# 세션을 따로 열고, 끝나면 만든 보호자 · 아이를 지운다 (기록은 CASCADE). API 는 `client` 로 부른다 —
# 핸들러도 자기 세션으로 커밋해야 겹친다.

_RACE_TOKEN = "test-token-observation-race"


@pytest.fixture
async def committed_record():
    """(Authorization 헤더, 아이 id, 기록 id) — 실제로 커밋된 값."""
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as setup:
        parent = Parent()
        setup.add(parent)
        await setup.flush()
        await seed_login(setup, parent_id=parent.id, token=_RACE_TOKEN)
        child_id = await link_child(setup, parent_id=parent.id)
        record = await _observe(setup, child_id, parent.id, "food")
        await setup.commit()
    yield {"Authorization": f"Bearer {_RACE_TOKEN}"}, child_id, record.id
    async with factory() as cleanup:
        await cleanup.execute(delete(Child).where(Child.id == child_id))
        await cleanup.execute(delete(Parent).where(Parent.id == parent.id))
        await cleanup.commit()


async def test_same_key_overlapping_is_204_both_times(
    client: AsyncClient, committed_record, curator_calls, monkeypatch
):
    """같은 키가 겹쳐 오면 뒤의 것은 아이 잠금에서 기다렸다가 처음 응답을 재생받는다 (#774).

    앞의 요청이 키를 적는 순간에 멈춰 두고 뒤의 요청을 보낸다. 키를 commit 뒤에 적으면 그때
    잠금은 이미 풀려 있어 뒤의 것이 기다리지 않고 지우러 들어가 404 를 낸다 — 아래 잠금 대기
    확인이 실패한다.
    """
    headers, cid, oid = committed_record
    url, keyed = _url(cid, oid), _key(headers, "same-key")
    writing, release = asyncio.Event(), asyncio.Event()
    remember, lock = idempotency.remember, observations.lock_child_memory
    locking: list[int] = []

    async def held(**kwargs):
        writing.set()
        await release.wait()
        await remember(**kwargs)

    async def tracked(db, child_id):
        locking.append(await backend_pid(db))
        await lock(db, child_id)

    monkeypatch.setattr(idempotency, "remember", held)
    monkeypatch.setattr(observations, "lock_child_memory", tracked)

    first = asyncio.create_task(client.delete(url, headers=keyed))
    await asyncio.wait_for(writing.wait(), timeout=5)
    second = asyncio.create_task(client.delete(url, headers=keyed))
    await wait_for_lock_wait(blocker=locking[0])  # 뒤의 것이 앞의 것이 쥔 아이 잠금에서 기다린다
    release.set()
    responses = await asyncio.wait_for(asyncio.gather(first, second), timeout=5)

    assert [r.status_code for r in responses] == [204, 204]
    assert curator_calls == [cid]  # 지운 것은 앞의 요청 하나다


async def test_deleted_health_record_leaves_the_symptom_repeat_count(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID, curator_calls
):
    """기침 사흘 → 하루를 지우면 이틀 — 반복 안내(7일 안 3일)에 닿지 않는다."""
    headers, pid = bearer
    days = [await _observe(session, cid, pid, "health", days_ago=ago) for ago in (0, 1, 2)]

    resp = await db_client.delete(
        _url(cid, days[1].id, "observation_health"), headers=_key(headers)
    )

    assert resp.status_code == 204
    today = today_kst()
    reader = StoreHealthMemory(DbMemoryStore(session, child_id=cid, today=today))
    found = await reader.health_observations(
        child_id=cid, date_from=today - timedelta(days=6), date_to=today
    )
    counted = symptom_days(found, today=today, timezone=ZoneInfo("Asia/Seoul"))
    assert [len(d) for d in counted.values()] == [2]


def test_swagger_documents_204_and_the_errors():
    from app.main import app

    op = app.openapi()["paths"]["/api/v1/children/{cid}/observations/{kind}/{oid}"]["delete"]
    assert set(op["responses"]) >= {"204", "400", "403", "404", "503"}
    assert any(p["name"] == "idempotency-key" for p in op["parameters"])
