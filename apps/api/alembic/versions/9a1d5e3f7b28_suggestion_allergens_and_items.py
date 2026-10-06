"""suggestion 에 allergens · items 를 더한다

allergens — 매칭되는 health_safety 행이 없는 알레르기(unknown)는 필터에서 거르지 않고
  추천을 승인할 때 안내한다. 그러려면 추천에 무엇이 들어 있는지 남아 있어야 하는데
  content · reason 은 글이라 백엔드가 읽을 수 없다.
items — 추천을 일정으로 만들 때 준비물(event_item.item_name) 하나씩.

Revision ID: 9a1d5e3f7b28
Revises: 4c7e2b9d1a63
Create Date: 2026-10-05
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "9a1d5e3f7b28"
down_revision: Union[str, Sequence[str], None] = "4c7e2b9d1a63"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "suggestion",
        sa.Column("allergens", postgresql.ARRAY(sa.Text()), server_default="{}", nullable=False),
    )
    op.add_column(
        "suggestion",
        sa.Column("items", postgresql.ARRAY(sa.Text()), server_default="{}", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("suggestion", "items")
    op.drop_column("suggestion", "allergens")
