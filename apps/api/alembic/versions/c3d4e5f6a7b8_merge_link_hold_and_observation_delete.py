"""merge_link_hold_and_observation_delete

Revision ID: c3d4e5f6a7b8
Revises: b4d8f2a6c913, b901cf42a99f
Create Date: 2026-09-30 11:57:10.009564

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'c3d4e5f6a7b8'
down_revision: Union[str, Sequence[str], None] = ('b4d8f2a6c913', 'b901cf42a99f')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
