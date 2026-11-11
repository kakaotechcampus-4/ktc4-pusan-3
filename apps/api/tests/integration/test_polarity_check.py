"""polarity CHECK — 프로필 -1 · 1 · NULL, 관찰 -1 · 0 · 1 (#287).

잘못된 값은 raw SQL 로 넣는다. ORM 을 거치면 DB 제약이 살아 있는지 확인하지 못한다
(test_correction_target 과 같은 이유).
"""

from datetime import date
from pathlib import Path
from runpy import run_path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.exc import IntegrityError

from app.domains.child.models import Child
from app.domains.identity.models import Parent
from app.domains.memory.observation.models import OBSERVATION_POLARITY_CHECK
from app.domains.memory.profile.models import (
    PROFILE_CONFIRMED_POLARITY_CHECK,
    PROFILE_POLARITY_CHECK,
)

MIGRATION = (
    Path(__file__).resolve().parents[2] / "alembic" / "versions" / "496fd5c10cd1_polarity_checks.py"
)

OBSERVATIONS = {
    "observation_food": {},
    "observation_education": {"topic": "'synthetic topic'"},
    "observation_activity": {"activity": "'synthetic activity'"},
    "observation_routine": {"routine_category": "'habit'"},
}


@pytest.fixture
async def child(session):
    owner = Parent()
    session.add(owner)
    await session.flush()
    record = Child(owner_parent_id=owner.id, nickname="test child", birth_date=date(2023, 1, 1))
    session.add(record)
    await session.flush()
    return record


def insert_profile(*, state: str, polarity: str, merge_key: str = "synthetic") -> sa.TextClause:
    return sa.text(
        "INSERT INTO profile_affinity"
        " (child_id, merge_key, domain, state, polarity, last_observed_on)"
        f" VALUES (:child_id, '{merge_key}', 'food', '{state}', {polarity}, '2026-09-01')"
        " RETURNING id"
    )


def insert_observation(table: str, *, polarity: int) -> sa.TextClause:
    extra = OBSERVATIONS[table]
    columns = ["child_id", "raw_text", "subject", "polarity", "confidence_source", "observed_range"]
    values = [
        ":child_id",
        "'synthetic record'",
        "'synthetic subject'",
        str(polarity),
        "'parent_direct'",
        "'[2026-09-01,2026-09-02)'::daterange",
    ]
    return sa.text(
        f"INSERT INTO {table} ({', '.join([*columns, *extra])})"
        f" VALUES ({', '.join([*values, *extra.values()])})"
    )


async def rejects(session, statement: sa.TextClause, child_id) -> None:
    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            await session.execute(statement, {"child_id": child_id})


async def test_프로필은_polarity_0_을_거부한다(session, child):
    await rejects(session, insert_profile(state="candidate", polarity="0"), child.id)


async def test_confirmed_프로필은_polarity_null_을_거부한다(session, child):
    await rejects(session, insert_profile(state="confirmed", polarity="NULL"), child.id)


@pytest.mark.parametrize("table", OBSERVATIONS)
async def test_관찰은_polarity_범위_밖을_거부한다(session, child, table):
    await rejects(session, insert_observation(table, polarity=2), child.id)


def test_모델과_마이그레이션이_같은_CHECK_문장을_쓴다():
    migration = run_path(str(MIGRATION))
    assert migration["PROFILE_POLARITY_CHECK"] == PROFILE_POLARITY_CHECK
    assert migration["PROFILE_CONFIRMED_POLARITY_CHECK"] == PROFILE_CONFIRMED_POLARITY_CHECK
    assert migration["OBSERVATION_POLARITY_CHECK"] == OBSERVATION_POLARITY_CHECK


async def test_마이그레이션은_걸리는_행을_정리하고_CHECK_를_건다(session, child):
    """downgrade 로 CHECK 를 푼 뒤 걸리는 행을 심고 upgrade 한다 — 운영 DB 가 겪는 순서다."""
    connection = await session.connection()
    params = {"child_id": child.id}

    def verify(conn):
        migration = run_path(str(MIGRATION))
        with Operations.context(MigrationContext.configure(conn)):
            migration["downgrade"]()

            def returning(statement, **extra):
                return conn.execute(statement, params | extra).scalar_one()

            zero = returning(insert_profile(state="candidate", polarity="0", merge_key="zero"))
            kept = returning(insert_profile(state="candidate", polarity="1", merge_key="kept"))
            unknown = returning(insert_profile(state="confirmed", polarity="NULL"))
            neutral = returning(
                sa.text(
                    "INSERT INTO observation_food (child_id, raw_text, subject, polarity,"
                    " confidence_source, observed_range, affinity_id, embedding)"
                    " VALUES (:child_id, 'synthetic record', 'zero', 0, 'parent_direct',"
                    " '[2026-09-01,2026-09-02)'::daterange, :zero,"
                    " array_fill(0.1::real, ARRAY[1536])::vector) RETURNING id"
                ),
                zero=zero,
            )
            liked = returning(
                sa.text(
                    "INSERT INTO observation_food (child_id, raw_text, subject, polarity,"
                    " confidence_source, observed_range) VALUES (:child_id, 'synthetic record',"
                    " 'liked', 1, 'parent_direct', '[2026-09-01,2026-09-02)'::daterange)"
                    " RETURNING id"
                )
            )
            for obs in (neutral, liked):
                conn.execute(
                    sa.text(
                        "INSERT INTO observation_link_hold"
                        " (child_id, domain, observation_id, uncertain_count, subject_hash)"
                        " VALUES (:child_id, 'food', :obs, 1, 'synthetic')"
                    ),
                    params | {"obs": obs},
                )

            migration["upgrade"]()

        profiles = dict(
            conn.execute(
                sa.text("SELECT id, state FROM profile_affinity WHERE child_id = :child_id"),
                params,
            ).all()
        )
        assert profiles == {kept: "candidate", unknown: "candidate"}, "0 은 지우고 NULL 은 내린다"
        cleared = conn.execute(
            sa.text(
                "SELECT affinity_id IS NULL AND embedding IS NULL FROM observation_food"
                " WHERE id = :obs"
            ),
            {"obs": neutral},
        ).scalar_one()
        assert cleared, "관찰은 남고 연결 · 벡터만 비운다"
        holds = conn.execute(
            sa.text("SELECT observation_id FROM observation_link_hold WHERE child_id = :child_id"),
            params,
        ).scalars()
        assert list(holds) == [liked], "0 관찰의 보류 기록만 지운다"
        with pytest.raises(IntegrityError):
            with conn.begin_nested():
                conn.execute(insert_profile(state="candidate", polarity="0"), params)

    await connection.run_sync(verify)
