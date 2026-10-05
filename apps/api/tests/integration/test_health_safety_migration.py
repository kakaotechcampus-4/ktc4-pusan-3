"""health_safety 10/4 확정안 마이그레이션을 격리된 스키마에서 검증한다."""

from pathlib import Path
from runpy import run_path
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text

MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "4c7e2b9d1a63_health_safety_confirmed_schema.py"
)

# 70e8ab2bf4b9 가 만든 모양 중 이 revision 이 건드리는 칸만 둔다. CHECK 이름도 실제 DB와 같다.
OLD_TABLE = """
    CREATE TABLE health_safety (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        kind varchar(32) NOT NULL CONSTRAINT safety_kind CHECK (kind IN (
            'allergy', 'chronic_disease', 'dietary_restriction',
            'behavioral', 'environmental', 'other_medical')),
        label text NOT NULL,
        aliases text[] NOT NULL DEFAULT '{}',
        category text,
        severity varchar(32) CONSTRAINT safety_severity CHECK (severity IN (
            'mild', 'moderate', 'severe', 'anaphylaxis')),
        management jsonb NOT NULL DEFAULT '{}',
        state varchar(32) NOT NULL DEFAULT 'active'
            CONSTRAINT safety_state CHECK (state IN ('active', 'retracted'))
    )
"""


def _isolate(conn, prefix: str) -> None:
    schema = f"{prefix}_{uuid4().hex}"
    conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    conn.execute(text(f'SET LOCAL search_path TO "{schema}", public'))
    conn.execute(text(OLD_TABLE))


def _rows(conn) -> dict:
    rows = conn.execute(
        text("SELECT *, management::text AS management_text FROM health_safety")
    ).mappings()
    return {row["label"]: row for row in rows}


async def test_확정안으로_옮기고_되돌린다(session):
    connection = await session.connection()

    def verify(conn):
        _isolate(conn, "safety_migration")
        conn.execute(
            text("""
            INSERT INTO health_safety
              (kind, label, aliases, category, severity, management, state) VALUES
              ('allergy', '땅콩', '{피넛}', '식품', NULL, '{}', 'retracted'),
              ('allergy', '쑥', '{}', '환경', NULL, '{}', 'active'),
              ('allergy', '키위', '{}', '과일', NULL, '{}', 'active'),
              ('chronic_disease', '소아 당뇨', '{}', '내분비', 'moderate',
               '{"insulin": "식전"}', 'active')
        """)
        )
        migration = run_path(str(MIGRATION))
        with Operations.context(MigrationContext.configure(conn)):
            migration["upgrade"]()
            after = _rows(conn)
            assert "aliases" not in after["땅콩"]
            assert "state" not in after["땅콩"]
            assert after["땅콩"]["status"] == "retracted"
            assert after["땅콩"]["category"] == ["food"]
            assert after["땅콩"]["management"] is None
            assert after["쑥"]["category"] == ["environment"]
            assert after["키위"]["category"] == []  # 세 값 밖의 자유 텍스트
            assert after["소아 당뇨"]["category"] == []  # 알레르기가 아닌 행
            assert after["소아 당뇨"]["management_text"] == '{"insulin": "식전"}'
            assert after["소아 당뇨"]["severity"] == "moderate"  # 알레르기가 아니면 그대로

            conn.execute(text("UPDATE health_safety SET status = 'none' WHERE label = '쑥'"))
            conn.execute(text("UPDATE health_safety SET severity = 'class_3' WHERE label = '땅콩'"))

            migration["downgrade"]()
            before = _rows(conn)
            assert before["땅콩"]["state"] == "retracted"
            assert before["쑥"]["state"] == "retracted"  # none 은 옛 값에 없다
            assert before["땅콩"]["category"] == "식품"
            assert before["키위"]["category"] is None
            assert before["땅콩"]["aliases"] == []
            assert before["땅콩"]["management_text"] == "{}"
            assert before["소아 당뇨"]["management_text"].startswith('{"text":')
            assert before["땅콩"]["severity"] is None  # class 값은 옛 CHECK 에 없다
            assert before["소아 당뇨"]["severity"] == "moderate"

    await connection.run_sync(verify)


async def test_dietary_restriction_행이_있으면_멈춘다(session):
    connection = await session.connection()

    def verify(conn):
        _isolate(conn, "safety_dietary")
        conn.execute(
            text(
                "INSERT INTO health_safety (kind, label) "
                "VALUES ('dietary_restriction', '합성 라벨')"
            )
        )
        migration = run_path(str(MIGRATION))
        with Operations.context(MigrationContext.configure(conn)):
            with pytest.raises(RuntimeError, match="dietary_restriction"):
                migration["upgrade"]()
        columns = conn.execute(text("SELECT * FROM health_safety")).mappings().one()
        assert columns["kind"] == "dietary_restriction"
        assert "aliases" in columns  # 아무것도 안 바뀌었다

    await connection.run_sync(verify)


async def test_알레르기에_옛_severity_가_있으면_멈춘다(session):
    """알레르기 행의 mild~anaphylaxis 는 Class 로 옮길 근거가 없다.

    보호자가 넣은 값이라 지우지도 않는다.
    """
    connection = await session.connection()

    def verify(conn):
        _isolate(conn, "safety_severity")
        conn.execute(
            text(
                "INSERT INTO health_safety (kind, label, severity) "
                "VALUES ('allergy', '우유', 'severe')"
            )
        )
        migration = run_path(str(MIGRATION))
        with Operations.context(MigrationContext.configure(conn)):
            with pytest.raises(RuntimeError, match="severity"):
                migration["upgrade"]()
        columns = conn.execute(text("SELECT * FROM health_safety")).mappings().one()
        assert columns["severity"] == "severe"
        assert "aliases" in columns  # 아무것도 안 바뀌었다

    await connection.run_sync(verify)
