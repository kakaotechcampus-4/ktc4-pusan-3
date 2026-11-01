"""recipe_catalog_source CHECK 마이그레이션을 격리된 스키마에서 올리고 내린다 (#510)."""

from pathlib import Path
from runpy import run_path
from uuid import uuid4

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.domains.food.models import RECIPE_SOURCE_CHECK

VERSIONS = Path(__file__).resolve().parents[2] / "alembic" / "versions"

# 5f3a8c1e9b27 이 만든 CHECK 중 이 revision 이 건드리는 것만 둔다. CHECK 이름도 실제 DB와 같다.
OLD_TABLE = """
    CREATE TABLE recipe_catalog (
        source varchar(16) NOT NULL,
        source_id text NOT NULL,
        PRIMARY KEY (source, source_id),
        CONSTRAINT recipe_catalog_source CHECK (source IN ('mafra_recipe', 'mfds_recipe'))
    )
"""


def _migration() -> dict:
    [file] = VERSIONS.glob("*_recipe_catalog_source_nics.py")
    return run_path(str(file))


def _isolate(conn) -> None:
    schema = f"recipe_source_{uuid4().hex}"
    conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    conn.execute(text(f'SET LOCAL search_path TO "{schema}", public'))
    conn.execute(text(OLD_TABLE))


def _insert(conn, source: str, source_id: str) -> None:
    conn.execute(
        text("INSERT INTO recipe_catalog (source, source_id) VALUES (:source, :source_id)"),
        {"source": source, "source_id": source_id},
    )


def _rejected(conn, source: str) -> bool:
    """CHECK 가 이 값을 막는가. 막혀도 바깥 트랜잭션이 죽지 않게 savepoint 로 시도한다."""
    try:
        with conn.begin_nested():
            _insert(conn, source, uuid4().hex)
    except IntegrityError:
        return True
    return False


def test_마이그레이션의_CHECK_는_모델과_같은_글자다():
    # 마이그레이션은 앱 코드를 import 하지 않고 글자를 박아 둔다. 모델만 고치면 DB 가 안 따라온다
    assert _migration()["RECIPE_SOURCE"] == RECIPE_SOURCE_CHECK


async def test_올리면_nics_recipe_를_받고_모르는_값은_계속_막는다(session):
    connection = await session.connection()
    migration = _migration()

    def verify(conn):
        _isolate(conn)
        assert _rejected(conn, "nics_recipe")  # 올리기 전에는 막힌다
        with Operations.context(MigrationContext.configure(conn)):
            migration["upgrade"]()
        assert not _rejected(conn, "nics_recipe")
        assert not _rejected(conn, "mafra_recipe")
        assert not _rejected(conn, "mfds_recipe")  # 처음부터 받던 값은 그대로다
        assert _rejected(conn, "unknown_recipe")

    await connection.run_sync(verify)


async def test_내리면_nics_recipe_행을_지우고_옛_CHECK_로_돌아간다(session):
    connection = await session.connection()
    migration = _migration()

    def verify(conn):
        _isolate(conn)
        with Operations.context(MigrationContext.configure(conn)):
            migration["upgrade"]()
            _insert(conn, "mafra_recipe", "m1")
            _insert(conn, "mfds_recipe", "f1")
            _insert(conn, "nics_recipe", "n1")

            migration["downgrade"]()

        rows = conn.execute(
            text("SELECT source, source_id FROM recipe_catalog ORDER BY 1, 2")
        ).all()
        # 받는 쪽이 다시 받을 수 있는 공용 캐시만 지운다 — 다른 소스 행은 그대로다
        assert [tuple(row) for row in rows] == [("mafra_recipe", "m1"), ("mfds_recipe", "f1")]
        assert _rejected(conn, "nics_recipe")

    await connection.run_sync(verify)
