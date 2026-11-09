"""merge event_series and location_use_log heads

Revision ID: 36d10aab173c
Revises: a7d3e9b1c246, e4b19c07d2a6
Create Date: 2026-11-08 22:42:14.099046

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = '36d10aab173c'
down_revision: Union[str, Sequence[str], None] = ('a7d3e9b1c246', 'e4b19c07d2a6')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
