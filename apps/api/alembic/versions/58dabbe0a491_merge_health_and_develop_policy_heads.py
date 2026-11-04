"""merge health and develop policy heads

Revision ID: 58dabbe0a491
Revises: b9f41f0457df, a3f9c2d17b40
Create Date: 2026-11-04 14:15:59.876670

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = '58dabbe0a491'
down_revision: Union[str, Sequence[str], None] = ('b9f41f0457df', 'a3f9c2d17b40')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
