"""merge place table and health medication branches

Revision ID: b9f41f0457df
Revises: 5bdc0aad2e70, e5b1d7c3a9f2
Create Date: 2026-11-01 05:52:03.115291

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'b9f41f0457df'
down_revision: Union[str, Sequence[str], None] = ('5bdc0aad2e70', 'e5b1d7c3a9f2')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
