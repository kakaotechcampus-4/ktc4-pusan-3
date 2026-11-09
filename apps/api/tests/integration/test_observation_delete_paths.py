"""기록을 지우는 세 길이 같은 결과를 내는지 (#736).

    store  — Memory Agent 가 대화에서 지울 때 (store `delete_observation` + `flush_profiles`)
    confirm — 대화 삭제 카드의 확정 (#712 `POST …/delete-proposals/{pid}/confirm`)
    api    — 기록 탭의 삭제 (`DELETE …/observations/{kind}/{oid}`)

세 길 모두 store 함수 하나로 지운다. 같은 기록을 심은 아이 셋에서 하나씩 지우고, 지운 뒤의 모습
(상태 · 검색 벡터 · Curator 보류 기록 · 기억 상태 · 강도 · 마지막 관찰일 · Curator 가 다시 줍는지)이
셋 다 같은지 본다. Curator 를 띄우는 자리만 다르다(store 는 러너가 띄운다).
검색 벡터가 있는 종류(식사 · 교육 · 놀이)와 없는 종류(생활 · 건강)를 나눈다.

보호자 한 명은 아이 하나에만 연결되므로 길마다 보호자를 따로 만든다.
발화 · 아이 정보는 전부 가짜 값이다 (루트 CLAUDE.md §9).
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import Range
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import idempotency
from app.api.quota import today_kst
from app.api.runs import runner
from app.domains.delete_proposal.repository import ProposalTab, add_proposal
from app.domains.memory.curator.db_store import DbCuratorStore
from app.domains.memory.observation.models import (
    ConfidenceSource,
    ObservationLinkHold,
    ObservationStatus,
    RoutineCategory,
)
from app.domains.memory.observation.repository import _MODEL_BY_DOMAIN, create_observation
from app.domains.memory.profile.models import MemoryDomain, ProfileAffinity, ProfileState
from app.domains.memory.store.db_store import DbMemoryStore
from tests.integration.api.conftest import issue_bearer, link_child

PATHS = ("store", "confirm", "api")
EMBEDDED = ("food", "education", "activity")
NOT_EMBEDDED = ("routine", "health")

_FIELDS = {
    "food": {"subject": "사과", "action": "먹음", "polarity": 1},
    "education": {"subject": "그림책", "topic": "그림책", "polarity": 1},
    "activity": {"subject": "블록", "activity": "블록 쌓기", "polarity": 1},
    "routine": {"subject": "양치", "routine_category": RoutineCategory.SELF_CARE},
    "health": {"symptom": ["기침"]},
}
_VECTOR = [0.1] * 1536


@pytest.fixture(autouse=True)
def _clean_keys():
    idempotency.clear()
    yield
    idempotency.clear()


@pytest.fixture(autouse=True)
def curator_calls(monkeypatch) -> list[uuid.UUID]:
    """Curator 를 띄운 아이 — 실제로 돌리지 않고 센다.

    store 길은 Curator 를 띄우지 않는다(대화 run 의 러너가 commit 뒤에 띄운다).
    두 API 는 commit 뒤에 직접 띄운다.
    """
    calls: list[uuid.UUID] = []
    monkeypatch.setattr(
        runner, "curator_trigger", lambda: lambda child_id, today: calls.append(child_id)
    )
    return calls


async def _observe(session, cid, pid, domain: str, days_ago: int):
    day = today_kst() - timedelta(days=days_ago)
    return await create_observation(
        session,
        domain=domain,
        child_id=cid,
        source_writer=pid,
        raw_text=f"synthetic {domain}",
        observed_range=Range(day, day + timedelta(days=1), bounds="[)"),
        fields={"confidence_source": ConfidenceSource.PARENT_DIRECT, **_FIELDS[domain]},
    )


async def _seed(session: AsyncSession, cid, pid, domain: str) -> dict:
    """지울 기록(target) 하나.

    검색 벡터가 있는 종류는 기억에 묶인 이전 기록(kept)과 둘의 보류 기록을 더한다. kept 의 보류
    기록은 남아야 한다 — 같은 종류의 다른 기록까지 지우면 안 된다.
    """
    target = await _observe(session, cid, pid, domain, days_ago=1)
    seeded = {"target": target.id, "profile": None}
    if domain not in EMBEDDED:
        return seeded

    kept = await _observe(session, cid, pid, domain, days_ago=5)
    profile = ProfileAffinity(
        child_id=cid,
        domain=MemoryDomain(domain),
        polarity=1,
        merge_key=_FIELDS[domain]["subject"],
        state=ProfileState.CONFIRMED,
        strength=0.55,
        last_observed_on=today_kst() - timedelta(days=1),
    )
    session.add(profile)
    await session.flush()
    model = _MODEL_BY_DOMAIN[domain]
    await session.execute(
        update(model)
        .where(model.id.in_([target.id, kept.id]))
        .values(affinity_id=profile.id, embedding=_VECTOR)
    )
    session.add_all(
        [
            ObservationLinkHold(
                child_id=cid,
                domain=domain,
                observation_id=oid,
                uncertain_count=1,
                subject_hash="synthetic",
            )
            for oid in (target.id, kept.id)
        ]
    )
    await session.flush()
    seeded["profile"] = profile.id
    seeded["kept"] = kept.id
    return seeded


async def _delete(
    path: str, client: AsyncClient, session: AsyncSession, headers, cid, pid, domain, oid
) -> None:
    kind = f"observation_{domain}"
    if path == "store":
        store = DbMemoryStore(session, child_id=cid, today=today_kst())
        assert await store.delete_observation(domain=domain, observation_id=str(oid))
        await store.flush_profiles()
        return
    keyed = {**headers, "Idempotency-Key": uuid.uuid4().hex}
    if path == "confirm":
        proposal = await add_proposal(
            session,
            parent_id=pid,
            child_id=cid,
            run_id=uuid.uuid4().hex,
            tabs=[ProposalTab(kind, False, (str(oid),), (str(oid),))],
            now=datetime.now(UTC),
        )
        resp = await client.post(
            f"/api/v1/children/{cid}/delete-proposals/{proposal}/confirm",
            json={"ids": [str(oid)]},
            headers=keyed,
        )
        assert resp.status_code == 200
        return
    resp = await client.delete(f"/api/v1/children/{cid}/observations/{kind}/{oid}", headers=keyed)
    assert resp.status_code == 204


async def _after(session: AsyncSession, cid, domain: str, seeded: dict) -> dict:
    """지운 뒤의 모습. 아이마다 다른 id 는 빼고 비교할 수 있는 값만 담는다."""
    model = _MODEL_BY_DOMAIN[domain]
    row = await session.get(model, seeded["target"], populate_existing=True)
    snapshot = {"status": row.status}
    if domain not in EMBEDDED:
        return snapshot

    profile = await session.get(ProfileAffinity, seeded["profile"], populate_existing=True)
    curator = DbCuratorStore(session)
    target = str(seeded["target"])
    holds = {
        name: await session.scalar(
            select(func.count())
            .select_from(ObservationLinkHold)
            .where(ObservationLinkHold.observation_id == seeded[name])
        )
        for name in ("target", "kept")
    }
    return {
        **snapshot,
        "embedding": row.embedding,
        "holds": holds,
        "profile": (profile.state, round(profile.strength, 6), profile.last_observed_on),
        "curator_unembedded": target in {o.id for o in await curator.list_unembedded(child_id=cid)},
        "curator_unlinked": target
        in {
            o.id
            for o in await curator.list_unlinked(
                child_id=cid, min_observed_on=today_kst() - timedelta(days=30)
            )
        },
    }


async def _run_all_paths(db_client, session, domain: str, curator_calls) -> dict[str, dict]:
    results = {}
    for n, path in enumerate(PATHS):
        headers, pid = await issue_bearer(session, token=f"path-{n}")
        cid = await link_child(session, parent_id=pid)
        seeded = await _seed(session, cid, pid, domain)
        await _delete(path, db_client, session, headers, cid, pid, domain, seeded["target"])
        results[path] = {
            **await _after(session, cid, domain, seeded),
            "curator_started": curator_calls.count(cid),
        }
    return results


@pytest.mark.parametrize("domain", EMBEDDED)
async def test_three_paths_agree_on_embedded_kinds(
    db_client: AsyncClient, session, domain, curator_calls
):
    results = await _run_all_paths(db_client, session, domain, curator_calls)

    # Curator 를 띄우는 자리만 다르다 — store 는 러너가, 두 API 는 commit 뒤에 직접 띄운다
    started = {path: results[path].pop("curator_started") for path in PATHS}
    assert started == {"store": 0, "confirm": 1, "api": 1}
    assert results["confirm"] == results["store"]
    assert results["api"] == results["store"]
    after = results["store"]
    assert after["status"] is ObservationStatus.DELETED
    assert after["embedding"] is None
    assert after["holds"] == {"target": 0, "kept": 1}
    # 남은 기록은 5일 전 하루뿐이다 — 마지막 관찰일이 그날로 내려온다
    assert after["profile"][2] == today_kst() - timedelta(days=5)
    # 검색 벡터를 비웠어도 Curator 가 다시 줍지 않는다
    assert (after["curator_unembedded"], after["curator_unlinked"]) == (False, False)


@pytest.mark.parametrize("domain", NOT_EMBEDDED)
async def test_three_paths_agree_on_kinds_without_embedding(
    db_client: AsyncClient, session, domain, curator_calls
):
    results = await _run_all_paths(db_client, session, domain, curator_calls)

    assert results == {
        "store": {"status": ObservationStatus.DELETED, "curator_started": 0},
        "confirm": {"status": ObservationStatus.DELETED, "curator_started": 1},
        "api": {"status": ObservationStatus.DELETED, "curator_started": 1},
    }
