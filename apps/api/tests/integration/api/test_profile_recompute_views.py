"""기억 재계산 배치(#636) 뒤 — 보호자 화면과 Agent 가 읽는 기억이 실제로 바뀌는가.

배치 자체(전이 · 경계 · 실패)는 tests/integration/test_profile_recompute_batch.py 가 본다.
여기서는 배치가 왜 필요한지를 소비처 쪽에서 본다 — 기억 화면(GET /affinities)은 DB 의 state 를
그대로 읽고, Agent 기억 어댑터는 21일 archived 만 읽을 때 보정하며 14일 강등은 보정하지 않는다.
그래서 배치 전에는 낡은 값이 보이고, 배치 뒤에는 맞는 값이 보여야 한다.
"""

import uuid
from contextlib import asynccontextmanager
from datetime import timedelta

from httpx import AsyncClient
from sqlalchemy.dialects.postgresql import Range
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.quota import today_kst
from app.domains.memory.observation.models import ConfidenceSource, ObservationFood
from app.domains.memory.profile.agent_reader import DbAffinityReader
from app.domains.memory.profile.models import MemoryDomain, ProfileAffinity, ProfileState
from app.workers.profile_recompute import run_recompute
from tests.integration.api.conftest import Bearer


async def _confirmed(session, child_id, key, *, observed_days_ago) -> uuid.UUID:
    """저장될 때는 confirmed 였던 기억 — 관찰이 `observed_days_ago` 날마다 한 건."""
    today = today_kst()
    row = ProfileAffinity(
        child_id=child_id,
        merge_key=key,
        domain=MemoryDomain.FOOD,
        state=ProfileState.CONFIRMED,
        polarity=1,
        last_observed_on=today - timedelta(days=min(observed_days_ago)),
    )
    session.add(row)
    await session.flush()
    for ago in observed_days_ago:
        day = today - timedelta(days=ago)
        session.add(
            ObservationFood(
                child_id=child_id,
                raw_text="원문",
                subject=key,
                polarity=1,
                confidence_source=ConfidenceSource.PARENT_DIRECT,
                observed_range=Range(day, day + timedelta(days=1), bounds="[)"),
                affinity_id=row.id,
            )
        )
    await session.flush()
    return row.id


async def _screen(db_client, cid, headers) -> dict[str, str]:
    body = (await db_client.get(f"/api/v1/children/{cid}/affinities", headers=headers)).json()
    return {a["merge_key"]: a["state"] for a in body["affinities"]}


def _reader(session) -> DbAffinityReader:
    @asynccontextmanager
    async def open_session():
        yield session  # 테스트 트랜잭션 안에서 읽는다 — 바깥 rollback 이 지운다

    return DbAffinityReader(open_session)


async def test_기억_화면은_배치_뒤에_강등과_보관을_반영한다(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    headers, _ = bearer
    await _confirmed(session, cid, "14일 지난 성향", observed_days_ago=[15, 14, 13])
    await _confirmed(session, cid, "21일 지난 성향", observed_days_ago=[24, 23, 22])
    await _confirmed(session, cid, "요즘 성향", observed_days_ago=[2, 1, 0])

    before = await _screen(db_client, cid, headers)
    await run_recompute(session, today=today_kst())
    after = await _screen(db_client, cid, headers)

    # 배치 전 — 새 기록이 없어서 저장된 값 그대로 "확인됨" 셋이 보인다 (#636 의 증상)
    assert before == {
        "14일 지난 성향": "confirmed",
        "21일 지난 성향": "confirmed",
        "요즘 성향": "confirmed",
    }
    # 배치 뒤 — 강등은 후보로, 보관은 목록에서 빠진다(archived 제외)
    assert after == {"14일 지난 성향": "candidate", "요즘 성향": "confirmed"}


async def test_Agent_가_읽는_기억도_배치_뒤에_14일_강등이_반영된다(
    session: AsyncSession, cid: uuid.UUID
):
    """기억 다리는 21일 archived 만 그날 기준으로 보정한다 — 14일 강등은 배치만 맞춘다.

    배치 전에는 confirmed 로 읽혀 추천 근거 1순위(티어 1)가 된다.
    """
    pid = await _confirmed(session, cid, "14일 지난 성향", observed_days_ago=[15, 14, 13])

    async def state_seen_by_agent() -> str:
        rows = await _reader(session).affinities(child_id=cid, domains=("food",))
        return next(r.state for r in rows if r.id == pid)

    assert await state_seen_by_agent() == "confirmed"
    await run_recompute(session, today=today_kst())
    assert await state_seen_by_agent() == "candidate"
