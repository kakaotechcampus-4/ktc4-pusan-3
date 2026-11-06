"""복약 두 표 마이그레이션 — 격리된 스키마에서 왕복하고, 모델과 어긋나지 않는지 본다.

#537 두 표를 만드는 것과 #749 체크한 보호자 칸 · 알림 구독 표 · 알림 처리 시각 칸을 더하는 것을
각각 왕복한다.
"""

from datetime import UTC, date, datetime
from pathlib import Path
from runpy import run_path
from uuid import uuid4

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text

from app.domains.child.models import Child
from app.domains.identity.models import Parent
from app.infra.db import registry  # noqa: F401 — 모델이 메타데이터에 올라와야 비교가 된다
from app.infra.db.base import Base

VERSIONS = Path(__file__).resolve().parents[2] / "alembic" / "versions"
MIGRATION = VERSIONS / "c6e2a8d41f93_add_medication_tables.py"
CHECKED_BY = VERSIONS / "361f26c96b2e_add_medication_dose_checked_by.py"
SUBSCRIPTION = VERSIONS / "885b03bd5b61_add_medication_notice_subscription.py"
NOTICE_SENT = VERSIONS / "5c6e32685af8_add_medication_dose_notice_sent_at.py"
TABLES = ("medication_schedule", "medication_dose")
COMPARED = (*TABLES, "medication_notice_subscription")


def _exists(conn, schema: str, table: str) -> bool:
    return conn.execute(text("SELECT to_regclass(:name)"), {"name": f'"{schema}".{table}'}).scalar()


def _has_column(conn, schema: str, table: str, column: str) -> bool:
    return (
        conn.execute(
            text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_schema = :s AND table_name = :t AND column_name = :c"
            ),
            {"s": schema, "t": table, "c": column},
        ).first()
        is not None
    )


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
    assert set(COMPARED) <= Base.metadata.tables.keys()
    connection = await session.connection()

    def diff(conn):
        return compare_metadata(MigrationContext.configure(conn), Base.metadata)

    diffs = await connection.run_sync(diff)
    assert [d for d in diffs if any(t in repr(d) for t in COMPARED)] == []


async def test_체크한_보호자_칸을_올리고_내려도_기존_체크_기록은_남는다(session):
    """#749 — 이미 체크된 회차는 보호자가 빈 채로 남고, 내리면 칸만 사라지고 체크 시각은 남는다."""
    parent = Parent()
    session.add(parent)
    await session.flush()
    child = Child(owner_parent_id=parent.id, nickname="test", birth_date=date(2023, 1, 1))
    session.add(child)
    await session.flush()
    checked_at = datetime(2026, 10, 20, 0, 5, tzinfo=UTC)
    connection = await session.connection()

    def verify(conn):
        schema = f"medication_migration_{uuid4().hex}"
        conn.execute(text(f'CREATE SCHEMA "{schema}"'))
        # child · parent 는 public 에 있다 — FK 가 search_path 로 찾는다
        conn.execute(text(f'SET LOCAL search_path TO "{schema}", public'))
        tables, checked_by = run_path(str(MIGRATION)), run_path(str(CHECKED_BY))
        with Operations.context(MigrationContext.configure(conn)):
            tables["upgrade"]()
            schedule_id = conn.execute(
                text(
                    "INSERT INTO medication_schedule"
                    " (child_id, title, starts_on, ends_on, created_by)"
                    " VALUES (:child, '약', '2026-10-20', '2026-10-20', 'caregiver') RETURNING id"
                ),
                {"child": child.id},
            ).scalar_one()
            conn.execute(
                text(
                    "INSERT INTO medication_dose"
                    " (schedule_id, dose_date, timing_anchor, checked_at)"
                    " VALUES (:s, '2026-10-20', 'breakfast', :at)"
                ),
                {"s": schedule_id, "at": checked_at},
            )

            checked_by["upgrade"]()
            assert conn.execute(
                text("SELECT checked_at, checked_by FROM medication_dose")
            ).one() == (
                checked_at,
                None,
            )

            checked_by["downgrade"]()
            assert not _has_column(conn, schema, "medication_dose", "checked_by")
            assert conn.execute(text("SELECT checked_at FROM medication_dose")).scalar_one() == (
                checked_at
            )

    await connection.run_sync(verify)


async def test_알림_구독_표를_올리고_내린다(session):
    """#749 — 두 표와 체크한 보호자 칸 위에 구독 표를 만들고 지운다."""
    connection = await session.connection()

    def verify(conn):
        schema = f"medication_migration_{uuid4().hex}"
        conn.execute(text(f'CREATE SCHEMA "{schema}"'))
        # child · parent 는 public 에 있다 — FK 가 search_path 로 찾는다
        conn.execute(text(f'SET LOCAL search_path TO "{schema}", public'))
        steps = [run_path(str(m)) for m in (MIGRATION, CHECKED_BY, SUBSCRIPTION)]
        with Operations.context(MigrationContext.configure(conn)):
            for step in steps:
                step["upgrade"]()
            assert _exists(conn, schema, "medication_notice_subscription")

            steps[-1]["downgrade"]()
            assert not _exists(conn, schema, "medication_notice_subscription")
            assert all(_exists(conn, schema, t) for t in TABLES)

    await connection.run_sync(verify)


async def test_알림_처리_시각_칸과_발송용_부분_인덱스를_올리고_내린다(session):
    """#749 — 인덱스 조건은 모델과의 autogenerate 비교가 보지 않아 여기서 본다."""
    connection = await session.connection()

    def index_def(conn, schema: str) -> str | None:
        return conn.execute(
            text(
                "SELECT indexdef FROM pg_indexes"
                " WHERE schemaname = :s AND indexname = 'medication_dose_notice_due_idx'"
            ),
            {"s": schema},
        ).scalar()

    def verify(conn):
        schema = f"medication_migration_{uuid4().hex}"
        conn.execute(text(f'CREATE SCHEMA "{schema}"'))
        # child · parent 는 public 에 있다 — FK 가 search_path 로 찾는다
        conn.execute(text(f'SET LOCAL search_path TO "{schema}", public'))
        steps = [run_path(str(m)) for m in (MIGRATION, CHECKED_BY, SUBSCRIPTION, NOTICE_SENT)]
        with Operations.context(MigrationContext.configure(conn)):
            for step in steps:
                step["upgrade"]()
            assert _has_column(conn, schema, "medication_dose", "notice_sent_at")
            assert index_def(conn, schema).endswith(
                "(dose_date, scheduled_time)"
                " WHERE ((notice_sent_at IS NULL) AND (checked_at IS NULL))"
            )

            steps[-1]["downgrade"]()
            assert not _has_column(conn, schema, "medication_dose", "notice_sent_at")
            assert index_def(conn, schema) is None

    await connection.run_sync(verify)
