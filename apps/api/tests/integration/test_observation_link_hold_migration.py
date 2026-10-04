"""실제 마이그레이션의 데이터 이전을 격리된 스키마에서 검증한다."""

from pathlib import Path
from runpy import run_path
from uuid import uuid4

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text


async def test_세_도메인_hold의_데이터를_이전하고_복원한다(session):
    connection = await session.connection()

    def verify(conn):
        # 스키마 생성과 DDL 모두 session fixture 의 바깥 트랜잭션에서 롤백된다.
        schema = f"hold_migration_{uuid4().hex}"
        conn.execute(text(f'CREATE SCHEMA "{schema}"'))
        conn.execute(text(f'SET LOCAL search_path TO "{schema}", public'))
        conn.execute(text("CREATE TABLE child (id uuid PRIMARY KEY)"))
        child_id, observation_id = uuid4(), uuid4()
        conn.execute(text("INSERT INTO child VALUES (:id)"), {"id": child_id})
        domains = ("food", "activity", "education")
        for domain in domains:
            conn.execute(
                text(
                    f"CREATE TABLE observation_{domain} "
                    "(id uuid PRIMARY KEY, child_id uuid NOT NULL REFERENCES child(id))"
                )
            )
            conn.execute(
                text(f"INSERT INTO observation_{domain} VALUES (:id, :child_id)"),
                {"id": observation_id, "child_id": child_id},
            )

        versions = Path(__file__).resolve().parents[2] / "alembic" / "versions"
        old = run_path(str(versions / "b901cf42a99f_add_observation_link_hold.py"))
        new = run_path(str(versions / "c37a8e1d902f_simplify_observation_link_hold.py"))
        with Operations.context(MigrationContext.configure(conn)):
            old["upgrade"]()
            for count, domain in enumerate(domains, start=1):
                conn.execute(
                    text(f"""
                    INSERT INTO observation_link_hold
                        ({domain}_id, uncertain_count, subject_hash, created_at, updated_at)
                    VALUES (:id, :count, :hash, '2026-09-01T00:00:00Z', '2026-09-02T00:00:00Z')
                """),
                    {"id": observation_id, "count": count, "hash": domain},
                )
            before = (
                conn.execute(text("SELECT * FROM observation_link_hold ORDER BY id"))
                .mappings()
                .all()
            )

            new["upgrade"]()
            after = (
                conn.execute(text("SELECT * FROM observation_link_hold ORDER BY id"))
                .mappings()
                .all()
            )
            assert len(after) == 3
            for previous, current in zip(before, after, strict=True):
                domain = previous["subject_hash"]
                assert current["domain"] == domain
                assert current["child_id"] == child_id
                assert current["observation_id"] == observation_id
                for column in ("id", "uncertain_count", "subject_hash", "created_at", "updated_at"):
                    assert current[column] == previous[column]
                assert not any(f"{d}_id" in current for d in domains)

            new["downgrade"]()
            restored = (
                conn.execute(text("SELECT * FROM observation_link_hold ORDER BY id"))
                .mappings()
                .all()
            )
            assert [dict(row) for row in restored] == [dict(row) for row in before]

    await connection.run_sync(verify)
