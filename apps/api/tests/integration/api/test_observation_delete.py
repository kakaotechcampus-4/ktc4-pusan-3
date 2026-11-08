"""기록 삭제 API — `DELETE /children/{cid}/observations/{kind}/{oid}` (#736).

    ① deleted 가 아닌 기록은 상태와 상관없이 지운다 — 고친 기록(stand_alone · inactive)도
    ② owner · member 모두 지운다. 연결되지 않은 보호자는 403
    ③ 없는 종류 · 없는 기록 · 이미 지운 기록 · 다른 아이의 기록은 404, 키가 없으면 400
    ④ 같은 키로 다시 오면 처음처럼 204 — 두 번 지우지 않는다. 잠금에서 기다린 사이 앞의 것이
       끝났어도 204 다
    ⑤ 지운 건강 기록은 증상 반복 안내의 날 수에서 빠진다

store 삭제 · #712 확정 API 와 결과가 같은지는
`tests/integration/test_observation_delete_paths.py` 가 본다.
발화 · 아이 정보는 전부 가짜 값이다 (루트 CLAUDE.md §9).
"""

import uuid
from datetime import timedelta
from zoneinfo import ZoneInfo

import pytest
from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.dialects.postgresql import Range
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.health.hook import symptom_days
from app.agents.memory_bridge import StoreHealthMemory
from app.api import idempotency
from app.api.quota import today_kst
from app.api.v1.routers import observations
from app.domains.memory.observation.models import (
    ConfidenceSource,
    ObservationFood,
    ObservationStatus,
    RoutineCategory,
)
from app.domains.memory.observation.repository import create_observation
from app.domains.memory.store.db_store import DbMemoryStore
from tests.integration.api.conftest import Bearer, issue_bearer, link_child

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
def curator_calls(monkeypatch):
    calls: list[uuid.UUID] = []
    monkeypatch.setattr(
        observations, "curator_trigger", lambda: lambda child_id, today: calls.append(child_id)
    )
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
    [ObservationStatus.ACTIVE, ObservationStatus.STAND_ALONE, ObservationStatus.INACTIVE],
)
async def test_deletes_whatever_the_status_and_starts_curator(
    db_client: AsyncClient,
    session: AsyncSession,
    bearer: Bearer,
    cid: uuid.UUID,
    curator_calls,
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


async def test_same_key_finished_while_waiting_for_the_lock_is_204(
    db_client: AsyncClient,
    session: AsyncSession,
    bearer: Bearer,
    cid: uuid.UUID,
    curator_calls,
    monkeypatch,
):
    """같은 키가 동시에 두 번 오면 뒤의 것은 아이 잠금에서 기다린다. 그사이 앞의 것이 지우고
    키를 적었으면 뒤의 것도 204 다 — 잠금을 얻은 뒤 키를 다시 보지 않으면 404 가 뜬다.

    잠금 대기를 흉내 낸다: 잠금을 얻는 순간 앞의 요청이 끝난 상태(지움 · 키 적음)로 만든다.
    """
    headers, pid = bearer
    record = await _observe(session, cid, pid, "food")
    url = _url(cid, record.id)
    locked = observations.lock_child_memory

    async def first_request_finishes_meanwhile(db, child_id):
        await locked(db, child_id)
        assert await DbMemoryStore(db, child_id=child_id).delete_observation(
            domain="food", observation_id=str(record.id)
        )
        await idempotency.remember(
            parent_id=pid, method="DELETE", path=url, key="same-key", replay={"status_code": 204}
        )

    monkeypatch.setattr(observations, "lock_child_memory", first_request_finishes_meanwhile)

    resp = await db_client.delete(url, headers=_key(headers, "same-key"))

    assert resp.status_code == 204
    assert curator_calls == []  # 지운 것은 앞의 요청이다


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
