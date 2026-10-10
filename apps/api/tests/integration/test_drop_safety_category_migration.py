"""health_safety.category 삭제 마이그레이션을 격리된 스키마에서 검증한다."""

from pathlib import Path
from runpy import run_path
from uuid import uuid4

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text

MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "d9e1f3a5b7c2_drop_health_safety_category.py"
)

# 4c7e2b9d1a63 이 만든 모양 중 이 revision 이 건드리는 칸만 둔다. CHECK 이름도 실제 DB와 같다.
OLD_TABLE = """
    CREATE TABLE health_safety (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        kind varchar(32) NOT NULL,
        label text NOT NULL,
        category varchar(32)[] DEFAULT '{}'
            CONSTRAINT health_safety_category_values
                CHECK (category <@ ARRAY['food', 'drug', 'environment']::varchar[]),
        CONSTRAINT health_safety_category_allergy_only
            CHECK (kind = 'allergy' OR coalesce(cardinality(category), 0) = 0)
    )
"""

CATEGORY_CHECKS = {"health_safety_category_values", "health_safety_category_allergy_only"}


def _isolate(conn) -> None:
    schema = f"safety_category_{uuid4().hex}"
    conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    conn.execute(text(f'SET LOCAL search_path TO "{schema}", public'))
    conn.execute(text(OLD_TABLE))


def _checks(conn) -> set[str]:
    rows = conn.execute(
        text(
            "SELECT conname FROM pg_constraint "
            "WHERE conrelid = 'health_safety'::regclass AND contype = 'c'"
        )
    )
    return set(rows.scalars())


async def test_category_칸과_CHECK_를_지우고_되돌린다(session):
    connection = await session.connection()

    def verify(conn):
        _isolate(conn)
        conn.execute(
            text("""
            INSERT INTO health_safety (kind, label, category) VALUES
              ('allergy', '쑥', '{food,environment}'),
              ('allergy', '페니실린', '{drug}'),
              ('chronic_disease', '소아 당뇨', '{}')
        """)
        )
        migration = run_path(str(MIGRATION))
        with Operations.context(MigrationContext.configure(conn)):
            migration["upgrade"]()
            row = conn.execute(text("SELECT * FROM health_safety WHERE label = '쑥'")).mappings()
            assert "category" not in row.one()
            assert _checks(conn) & CATEGORY_CHECKS == set()
            # 행은 그대로다 — 칸만 지운다
            assert conn.execute(text("SELECT count(*) FROM health_safety")).scalar() == 3

            migration["downgrade"]()
            rows = dict(conn.execute(text("SELECT label, category FROM health_safety")).all())
            # 칸과 CHECK 는 돌아오지만 값은 돌아오지 않는다 (손실)
            assert rows == {"쑥": [], "페니실린": [], "소아 당뇨": []}
            assert _checks(conn) >= CATEGORY_CHECKS

    await connection.run_sync(verify)
