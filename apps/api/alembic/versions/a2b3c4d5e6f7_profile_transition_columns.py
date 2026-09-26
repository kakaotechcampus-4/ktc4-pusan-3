"""profile_affinity 에 상태 전이 추적 컬럼을 더하고 strength 기본값을 0.5 로 바꾼다

last_transition_at / last_transition_from — 재계산 멱등성을 위해 마지막 전이를 기록한다.
전이 보너스(승격 +10%, 강등 -10%)가 중복 적용되지 않으려면 "직전 상태"를 알아야 하고,
같은 전이가 이미 반영됐는지를 판단하려면 "마지막 전이 시각"이 필요하다.

strength 기본값 0.3 → 0.5: 이슈 #149 스펙. 기존 행은 건드리지 않는다.

Revision ID: a2b3c4d5e6f7
Revises: c5a81f0d3b62
Create Date: 2026-09-26
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a2b3c4d5e6f7"
down_revision: Union[str, Sequence[str], None] = "c5a81f0d3b62"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "profile_affinity"


def upgrade() -> None:
    op.add_column(TABLE, sa.Column("last_transition_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(TABLE, sa.Column("last_transition_from", sa.String(32), nullable=True))
    op.alter_column(TABLE, "strength", server_default="0.5")


def downgrade() -> None:
    op.alter_column(TABLE, "strength", server_default="0.3")
    op.drop_column(TABLE, "last_transition_from")
    op.drop_column(TABLE, "last_transition_at")
