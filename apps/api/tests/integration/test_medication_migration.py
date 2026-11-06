"""복약 두 표 마이그레이션 — 격리된 스키마에서 왕복하고, 모델과 어긋나지 않는지 본다 (#537)."""

from pathlib import Path
from runpy import run_path
from uuid import uuid4

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text

from app.infra.db import registry  # noqa: F401 — 모델이 메타데이터에 올라와야 비교가 된다
from app.infra.db.base import Base

MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "c6e2a8d41f93_add_medication_tables.py"
)
TABLES = ("medication_schedule", "medication_dose")


def _exists(conn, schema: str, table: str) -> bool:
    return conn.execute(text("SELECT to_regclass(:name)"), {"name": f'"{schema}".{table}'}).scalar()


async def test_올리고_내리면_두_표가_생겼다_사라진다(session):
    connection = await session.connection()

    def verify(conn):
        schema = f"medication_migration_{uuid4().hex}"
        conn.execute(text(f'CREATE SCHEMA "{schema}"'))
        # child 는 public 에 있다 — FK 가 search_path 로 찾는다
        conn.execute(text(f'SET LOCAL search_path TO "{schema}", public'))
        migration = run_path(str(MIGRATION))
        with Operations.context(MigrationContext.configure(conn)):
            migration["upgrade"]()
            assert all(_exists(conn, schema, t) for t in TABLES)
            generated = conn.execute(
                text(
                    "SELECT is_generated FROM information_schema.columns "
                    "WHERE table_schema = :s AND table_name = 'medication_dose' "
                    "AND column_name = 'scheduled_time'"
                ),
                {"s": schema},
            ).scalar_one()
            assert generated == "ALWAYS"

            migration["downgrade"]()
            assert not any(_exists(conn, schema, t) for t in TABLES)

    await connection.run_sync(verify)


async def test_모델과_마이그레이션이_어긋나지_않는다(session):
    """autogenerate 가 복약 두 표에 대해 빈 diff 를 내야 한다.

    CHECK 와 생성 컬럼 식은 autogenerate 가 비교하지 않는다 — 그건 test_medication_tables 가
    실제 INSERT 로 확인한다. 여기는 칸 · 타입 · NULL · FK · 인덱스 · UNIQUE 와,
    registry 에 모델이 빠져 표 자체를 못 보는 경우를 잡는다.
    """
    # 모델도 표도 없으면 diff 가 비어 통과해 버린다 — registry 에 올라왔는지부터 본다
    assert set(TABLES) <= Base.metadata.tables.keys()
    connection = await session.connection()

    def diff(conn):
        return compare_metadata(MigrationContext.configure(conn), Base.metadata)

    diffs = await connection.run_sync(diff)
    assert [d for d in diffs if any(t in repr(d) for t in TABLES)] == []
