"""merge suggestion warnings and develop heads

Revision ID: dff7c4ee929d
Revises: 3399eb6864ff, 58dabbe0a491
Create Date: 2026-11-05 20:27:39.966989

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'dff7c4ee929d'
down_revision: Union[str, Sequence[str], None] = ('3399eb6864ff', '58dabbe0a491')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
