"""merge reminder/push_device and event status removal branches

Revision ID: 1421f6d856de
Revises: 0f4480ddc873, 1b7312ff8880
Create Date: 2026-09-20 19:56:10.880250

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = '1421f6d856de'
down_revision: Union[str, Sequence[str], None] = ('0f4480ddc873', '1b7312ff8880')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
