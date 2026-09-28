"""suggestion_evidence.source_updated_at 삭제

인용할 때 읽은 원본의 시각을 박아 두고 "추천이 본 것과 지금이 다른지" 를 비교하려던 칸이다.
그 비교를 읽는 코드가 없어 뺀다.

downgrade 는 NOT NULL 로 되살리면서 기존 행을 created_at 으로 채운다. 원래 값은 복구되지 않는다.

Revision ID: b4d8f2a6c913
Revises: a7e3c91d5f20
Create Date: 2026-09-29
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b4d8f2a6c913"
down_revision: Union[str, Sequence[str], None] = "a7e3c91d5f20"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column("suggestion_evidence", "source_updated_at")


def downgrade() -> None:
    op.add_column(
        "suggestion_evidence",
        sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute("UPDATE suggestion_evidence SET source_updated_at = created_at")
    op.alter_column("suggestion_evidence", "source_updated_at", nullable=False)
