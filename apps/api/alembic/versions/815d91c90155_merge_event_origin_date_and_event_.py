"""merge event_origin_date and event_series heads

Revision ID: 815d91c90155
Revises: 36d10aab173c, b3f6c8d2e471
Create Date: 2026-11-09 00:50:57.023771

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "815d91c90155"
down_revision: Union[str, Sequence[str], None] = ("36d10aab173c", "b3f6c8d2e471")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
