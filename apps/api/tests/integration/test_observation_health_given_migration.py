"""observation_health 체온 세 칸 · 먹인 약 두 칸 마이그레이션을 격리된 스키마에서 검증한다 (#551).

🚨 develop 머지는 배포(`deploy-down` → `alembic upgrade heads` → `deploy-up`)다. 증상이 빈 기존 행
   하나 때문에 업그레이드가 실패하면 서비스가 내려간 채로 남는다. 그래서 DB 는 "증상과 약이 한 행에
   같이 있다" 만 막는다 — 기존 행에는 늘 참이라 바로 VALIDATE 되고, 증상이 빈 옛 행도 계속 고칠 수
   있다. "둘 다 없다" 는 Memory 스키마가 막는다. 행은 지우지 않는다.
"""

from pathlib import Path
from runpy import run_path
from uuid import uuid4

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "e5b1d7c3a9f2_observation_health_temperature_and_given.py"
)

# c6e2a8d41f93 시점의 모양 중 이 revision 이 건드리는 칸만 둔다. symptom 에는 기본값이 없다.
OLD_TABLE = """
    CREATE TABLE observation_health (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        raw_text text NOT NULL,
        symptom text[] NOT NULL,
        observed_time timestamptz
    )
"""

NEW_COLUMNS = {
    "temperature",
    "measured_at",
    "measure_site",
    "medication_given",
    "linked_symptom_id",
}
CHECKS = {
    "observation_health_temperature_range",
    "observation_health_measure_site",
    "observation_health_symptom_not_with_medication",
    "observation_health_link_needs_medication",
    "observation_health_medication_no_temperature",
}


def _isolate(conn) -> None:
    schema = f"health_given_{uuid4().hex}"
    conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    conn.execute(text(f'SET LOCAL search_path TO "{schema}", public'))
    conn.execute(text(OLD_TABLE))


def _columns(conn) -> set[str]:
    rows = conn.execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = 'observation_health'"
        )
    )
    return set(rows.scalars())


def _checks(conn) -> dict[str, bool]:
    """CHECK 이름 → VALIDATE 됐는지."""
    rows = conn.execute(
        text(
            "SELECT conname, convalidated FROM pg_constraint "
            "WHERE conrelid = 'observation_health'::regclass AND contype = 'c'"
        )
    )
    return dict(rows.all())


def _rejected(conn, values: str) -> bool:
    """savepoint 안에서 넣어 본다. 밖에서 실패하면 테스트 트랜잭션이 통째로 죽는다."""
    try:
        with conn.begin_nested():
            conn.execute(text(f"INSERT INTO observation_health {values}"))
    except IntegrityError:
        return True
    return False


async def test_올리고_내리면_다섯_칸과_CHECK_가_생겼다_사라진다(session):
    connection = await session.connection()

    def verify(conn):
        _isolate(conn)
        conn.execute(
            text("INSERT INTO observation_health (raw_text, symptom) VALUES ('기침해', '{기침}')")
        )
        migration = run_path(str(MIGRATION))
        with Operations.context(MigrationContext.configure(conn)):
            migration["upgrade"]()
            assert _columns(conn) >= NEW_COLUMNS
            checks = _checks(conn)
            assert set(checks) >= CHECKS
            # 다섯 칸이 모두 새 칸이라 기존 행에는 늘 참이다 — 전부 같은 자리에서 VALIDATE 된다
            assert all(checks[name] for name in CHECKS)

            # 약 행 — symptom 을 안 넣으면 '{}' 가 들어간다
            medicine = conn.execute(
                text(
                    "INSERT INTO observation_health (raw_text, medication_given) "
                    "VALUES ('해열제 먹였어', '해열제') RETURNING id, symptom"
                )
            ).one()
            assert medicine.symptom == []
            # 증상과 약이 한 행에 같이 있지 않다
            assert _rejected(
                conn, "(raw_text, symptom, medication_given) VALUES ('x', '{발열}', '약')"
            )
            # 연결은 약 행에만
            assert _rejected(
                conn,
                f"(raw_text, symptom, linked_symptom_id) VALUES ('x', '{{발열}}', '{medicine.id}')",
            )
            assert not _rejected(
                conn,
                "(raw_text, medication_given, linked_symptom_id) "
                f"VALUES ('x', '약', '{medicine.id}')",
            )
            # 약 행에는 체온 세 칸이 없다
            for column, value in (
                ("temperature", "38.5"),
                ("measured_at", "now()"),
                ("measure_site", "'ear'"),
            ):
                assert _rejected(
                    conn, f"(raw_text, medication_given, {column}) VALUES ('x', '약', {value})"
                ), column
            assert not _rejected(
                conn,
                "(raw_text, symptom, temperature, measured_at, measure_site) "
                "VALUES ('x', '{발열}', 38.5, now(), 'ear')",
            )
            # 체온 범위 · 잰 부위
            assert _rejected(conn, "(raw_text, symptom, temperature) VALUES ('x', '{발열}', 45.1)")
            assert _rejected(
                conn, "(raw_text, symptom, measure_site) VALUES ('x', '{발열}', 'foot')"
            )
            # 연결된 증상 행이 지워지면 연결만 비운다
            linked = conn.execute(
                text(
                    "INSERT INTO observation_health (raw_text, symptom) VALUES ('열', '{발열}') "
                    "RETURNING id"
                )
            ).scalar_one()
            given = conn.execute(
                text(
                    "INSERT INTO observation_health "
                    "(raw_text, medication_given, linked_symptom_id) "
                    f"VALUES ('약', '해열제', '{linked}') RETURNING id"
                )
            ).scalar_one()
            conn.execute(text(f"DELETE FROM observation_health WHERE id = '{linked}'"))
            assert (
                conn.execute(
                    text(f"SELECT linked_symptom_id FROM observation_health WHERE id = '{given}'")
                ).scalar_one()
                is None
            )

            migration["downgrade"]()
            assert _columns(conn) & NEW_COLUMNS == set()
            assert set(_checks(conn)) & CHECKS == set()
            default = conn.execute(
                text(
                    "SELECT column_default FROM information_schema.columns "
                    "WHERE table_schema = current_schema() AND table_name = 'observation_health' "
                    "AND column_name = 'symptom'"
                )
            ).scalar_one()
            assert default is None

    await connection.run_sync(verify)


async def test_증상이_빈_기존_행이_있어도_업그레이드된다(session):
    connection = await session.connection()

    def verify(conn):
        _isolate(conn)
        # 지금의 수정 인자에는 최소 1개 제한이 없어 이런 행이 있을 수 있다
        conn.execute(
            text(
                "INSERT INTO observation_health (raw_text, symptom) VALUES "
                "('증상 비운 기록', '{}'), ('기침해', '{기침}')"
            )
        )
        migration = run_path(str(MIGRATION))
        with Operations.context(MigrationContext.configure(conn)):
            migration["upgrade"]()  # 여기서 실패하면 배포가 내려간 채로 남는다

            checks = _checks(conn)
            assert all(checks[name] for name in CHECKS)  # 옛 행이 있어도 전부 VALIDATE 된다
            # 보호자 기록이라 지우지 않는다
            assert conn.execute(text("SELECT count(*) FROM observation_health")).scalar() == 2
            # 증상이 빈 옛 행도 계속 고칠 수 있다 — soft delete · Correction 이 이 UPDATE 다
            with conn.begin_nested():
                conn.execute(
                    text("UPDATE observation_health SET raw_text = 'x' WHERE symptom = '{}'")
                )

            migration["downgrade"]()
            assert _columns(conn) & NEW_COLUMNS == set()

    await connection.run_sync(verify)
