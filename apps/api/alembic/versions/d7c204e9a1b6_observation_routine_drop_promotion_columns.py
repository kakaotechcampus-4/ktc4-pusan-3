"""observation_routine 에서 승격 세 칸을 뺀다

`profile_affinity.domain` 은 `food` / `activity` / `education` 셋뿐이라 routine 관찰이
올라갈 행이 만들어지지 않는다 (2026-09-23 결정). `affinity_id` 는 가리킬 대상이 없어
영구히 NULL 이고, `embedding` 은 어느 affinity 행에 붙일지 고르는 입력, `strong_signals`
는 승격 판정 근거라 셋 다 쓸모가 없다.

`subject` 와 `polarity` 는 남긴다 — routine 은 승격은 안 해도 관찰 자체가 티어 3 근거로
인용되고(Growth 의 routine_coaching), ObservationRow 가 그 두 칸을 요구한다.

ORM 에서는 세 칸을 Promotable 믹스인으로 갈라 두었다. routine 만 상속하지 않는다.
Memory tool 쪽도 같이 갈랐다 — ObservationRoutineCreate 가 strong_signals 를 더는 받지
않는다 (그대로 두면 model_dump() 가 없는 컬럼을 리포지토리로 내려보낸다).

저장된 행이 없어서 옮길 데이터는 없다.

Revision ID: d7c204e9a1b6
Revises: b3e7d9c05a12
Create Date: 2026-09-24
"""

from typing import Sequence, Union

import pgvector.sqlalchemy.vector
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d7c204e9a1b6"
down_revision: Union[str, Sequence[str], None] = "b3e7d9c05a12"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "observation_routine"
FK = "observation_routine_affinity_id_fkey"


def upgrade() -> None:
    # affinity_id 의 FK 는 컬럼과 함께 사라진다
    op.drop_column(TABLE, "affinity_id")
    op.drop_column(TABLE, "embedding")
    op.drop_column(TABLE, "strong_signals")


def downgrade() -> None:
    op.add_column(
        TABLE,
        sa.Column("embedding", pgvector.sqlalchemy.vector.VECTOR(dim=1536), nullable=True),
    )
    op.add_column(TABLE, sa.Column("affinity_id", sa.UUID(), nullable=True))
    op.add_column(
        TABLE,
        sa.Column(
            "strong_signals",
            postgresql.ARRAY(sa.Text()),
            server_default="{}",
            nullable=False,
        ),
    )
    op.create_foreign_key(
        FK, TABLE, "profile_affinity", ["affinity_id"], ["id"], ondelete="SET NULL"
    )
