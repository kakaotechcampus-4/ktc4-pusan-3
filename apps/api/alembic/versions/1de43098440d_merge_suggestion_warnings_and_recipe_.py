"""merge suggestion warnings and recipe source heads

Revision ID: 1de43098440d
Revises: dff7c4ee929d, e1d147d1b701
Create Date: 2026-11-06 16:54:53.337570

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = '1de43098440d'
down_revision: Union[str, Sequence[str], None] = ('dff7c4ee929d', 'e1d147d1b701')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
