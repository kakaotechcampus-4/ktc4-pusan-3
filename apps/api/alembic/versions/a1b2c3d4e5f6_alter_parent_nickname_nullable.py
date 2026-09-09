"""alter parent.nickname to nullable

Revision ID: a1b2c3d4e5f6
Revises: 70e8ab2bf4b9
Create Date: 2026-09-09 00:30:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "70e8ab2bf4b9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column("parent", "nickname", existing_type=sa.Text(), nullable=True)


def downgrade() -> None:
    op.alter_column("parent", "nickname", existing_type=sa.Text(), nullable=False)
