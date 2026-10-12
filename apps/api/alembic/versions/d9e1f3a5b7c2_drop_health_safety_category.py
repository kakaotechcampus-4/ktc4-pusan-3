"""health_safety.category 를 지운다

10/4 에 둔 알레르기 분류(food/drug/environment)를 지운다. 안전 필터는 active 알레르기를
전부 읽고, Agent 별 matcher 가 후보와 실제로 매칭되는 것만 거른다 (#295).

downgrade 는 손실이 있다 — 칸과 CHECK 는 되살아나지만 값은 전부 '{}'(분류 없음)이다.

Revision ID: d9e1f3a5b7c2
Revises: f8a2b3c4d5e6
Create Date: 2026-10-09
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d9e1f3a5b7c2"
down_revision: Union[str, Sequence[str], None] = "f8a2b3c4d5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "health_safety"
CATEGORY_VALUES = ("food", "drug", "environment")


def upgrade() -> None:
    op.drop_constraint("health_safety_category_allergy_only", TABLE, type_="check")
    op.drop_constraint("health_safety_category_values", TABLE, type_="check")
    op.drop_column(TABLE, "category")


def downgrade() -> None:
    op.add_column(
        TABLE,
        sa.Column(
            "category",
            sa.ARRAY(sa.String(32)),
            server_default=sa.text("'{}'"),
            nullable=True,
        ),
    )
    values = ", ".join(f"'{value}'" for value in CATEGORY_VALUES)
    op.create_check_constraint(
        "health_safety_category_values", TABLE, f"category <@ ARRAY[{values}]::varchar[]"
    )
    op.create_check_constraint(
        "health_safety_category_allergy_only",
        TABLE,
        "kind = 'allergy' OR coalesce(cardinality(category), 0) = 0",
    )
