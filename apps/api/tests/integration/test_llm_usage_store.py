"""LLM 사용량 저장 (#710) — 묶음 INSERT · 탈퇴 때의 SET NULL · 90일 정리.

세션은 바깥 트랜잭션 안의 savepoint 라 끝나면 롤백된다 (tests/conftest.py). 값은 전부 합성이다.
"""

import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import get_args

import pytest
from sqlalchemy import select

from app.core import llm_usage
from app.core.llm_usage import UsageRecord
from app.domains.identity.models import Parent
from app.domains.identity.withdraw import withdraw_parent
from app.domains.usage.models import LlmUsage
from app.domains.usage.repository import insert_usage, purge_expired_llm_usage

NOW = datetime(2026, 11, 9, 3, 0, tzinfo=UTC)


def _record(parent_id: uuid.UUID, **changed) -> UsageRecord:
    base = UsageRecord(
        parent_id=parent_id,
        run_id="run-1",
        source="input",
        role="food",
        provider="gateway.example",
        model="model-a",
        prompt_tokens=100,
        cached_prompt_tokens=40,
        completion_tokens=20,
        reasoning_tokens=5,
        latency_ms=1234,
        occurred_at=NOW,
    )
    return replace(base, **changed)


async def _parent(session) -> uuid.UUID:
    parent = Parent()
    session.add(parent)
    await session.flush()
    return parent.id


async def _rows(session, **where) -> list[LlmUsage]:
    stmt = select(LlmUsage).order_by(LlmUsage.role)
    for column, value in where.items():
        stmt = stmt.where(getattr(LlmUsage, column) == value)
    return list((await session.scalars(stmt)).all())


async def test_있는_보호자의_기록은_그대로_저장된다(session):
    parent_id = await _parent(session)
    records = [
        _record(parent_id, role="food"),
        _record(
            parent_id,
            role="supervisor",
            source="suggest",
            run_id="run-2",
            model="model-b",
            prompt_tokens=7,
            cached_prompt_tokens=0,
            completion_tokens=3,
            reasoning_tokens=0,
            latency_ms=9,
        ),
    ]

    assert await insert_usage(session, records) == 2

    rows = {row.role: row for row in await _rows(session, parent_id=parent_id)}
    assert set(rows) == {"food", "supervisor"}
    for record in records:
        row = rows[record.role]
        assert (
            row.parent_id,
            row.run_id,
            row.source,
            row.provider,
            row.model,
            row.prompt_tokens,
            row.cached_prompt_tokens,
            row.completion_tokens,
            row.reasoning_tokens,
            row.latency_ms,
            row.occurred_at,
        ) == (
            record.parent_id,
            record.run_id,
            record.source,
            record.provider,
            record.model,
            record.prompt_tokens,
            record.cached_prompt_tokens,
            record.completion_tokens,
            record.reasoning_tokens,
            record.latency_ms,
            record.occurred_at,
        )


async def test_없는_보호자의_기록은_보호자_칸만_비워_저장된다(session):
    parent_id = await _parent(session)
    ghost = _record(uuid.uuid4(), run_id="ghost-run", role="memory", prompt_tokens=55)

    # 한 묶음에 섞여 있어도 있는 보호자의 행이 같이 저장된다
    assert await insert_usage(session, [_record(parent_id), ghost]) == 2

    [row] = await _rows(session, run_id="ghost-run")
    assert row.parent_id is None
    assert (row.role, row.prompt_tokens) == ("memory", 55)
    assert len(await _rows(session, parent_id=parent_id)) == 1


async def test_탈퇴하면_보호자_칸만_비고_숫자는_남는다(session):
    parent_id = await _parent(session)
    await insert_usage(session, [_record(parent_id, run_id="run-w", prompt_tokens=321)])

    assert await withdraw_parent(session, parent_id=parent_id, now=NOW) is not None

    [row] = await _rows(session, run_id="run-w")
    assert row.parent_id is None
    assert (row.prompt_tokens, row.cached_prompt_tokens, row.completion_tokens) == (321, 40, 20)


async def test_빈_묶음은_DB_를_부르지_않고_0_이다(session):
    assert await insert_usage(session, []) == 0


@pytest.mark.parametrize("source", get_args(llm_usage.Source))
async def test_Source_값은_모두_CHECK_를_통과한다(session, source):
    parent_id = await _parent(session)

    assert (
        await insert_usage(session, [_record(parent_id, source=source, run_id=f"r-{source}")]) == 1
    )

    [row] = await _rows(session, run_id=f"r-{source}")
    assert row.source == source


async def test_90일이_지난_사용량만_지운다(session):
    parent_id = await _parent(session)
    await insert_usage(
        session,
        [
            _record(parent_id, run_id="old", occurred_at=NOW - timedelta(days=91)),
            _record(parent_id, run_id="fresh", occurred_at=NOW - timedelta(days=89)),
            # 탈퇴한 보호자의 행(parent_id NULL)도 같은 기준으로 지운다
            _record(uuid.uuid4(), run_id="old-withdrawn", occurred_at=NOW - timedelta(days=91)),
        ],
    )

    assert await purge_expired_llm_usage(session, now=NOW) == 2

    assert [row.run_id for row in await _rows(session)] == ["fresh"]
