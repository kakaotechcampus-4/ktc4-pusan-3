"""add observation query indexes

Revision ID: 9c8d7e6f5a4b
Revises: 1421f6d856de
Create Date: 2026-09-22

관찰 조회와 홈 집계는 항상 child_id로 범위를 제한하고 status='active'를 자주 사용한다.
5개 테이블에 같은 복합 인덱스를 두어 UNION ALL의 각 분기가 전체 테이블을 훑지 않게 한다.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "9c8d7e6f5a4b"
down_revision: str | Sequence[str] | None = "1421f6d856de"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OBSERVATION_TABLES = (
    "observation_food",
    "observation_health",
    "observation_education",
    "observation_activity",
    "observation_routine",
)


def upgrade() -> None:
    for table in OBSERVATION_TABLES:
        op.create_index(f"ix_{table}_child_status", table, ["child_id", "status"])


def downgrade() -> None:
    for table in reversed(OBSERVATION_TABLES):
        op.drop_index(f"ix_{table}_child_status", table_name=table)
