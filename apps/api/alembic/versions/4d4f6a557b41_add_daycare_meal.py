"""어린이집 급식 표 daycare_meal 을 만든다 (K 묶음 1/2)

설계 정본은 docs/agents/food/food_agent_own_table.md §5 다. 끼니는 기관이 정한 셋(lunch ·
snack_am · snack_pm), 같은 아이 · 같은 날 · 같은 끼니는 한 행, 아이가 지워지면 같이 지워진다.
급식표에 인쇄된 알레르기 번호는 칸이 없다 (10-09 — 저장하지 않는다).

Revision ID: 4d4f6a557b41
Revises: d5e8a1c4b937
Create Date: 2026-10-10
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "4d4f6a557b41"
down_revision: Union[str, Sequence[str], None] = "d5e8a1c4b937"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # CHECK 이름 · 식은 모델(enum_col_py)이 만드는 것과 같다
    op.create_table(
        "daycare_meal",
        sa.Column("id", sa.UUID(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("child_id", sa.UUID(), nullable=False),
        sa.Column("serve_date", sa.Date(), nullable=False),
        sa.Column("meal_slot", sa.String(32), nullable=False),
        sa.Column("menu_keys", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column(
            "amount_factor", sa.Numeric(3, 2), server_default=sa.text("1.00"), nullable=False
        ),
        sa.Column("amount_known", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "caregiver_checked", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("origin", sa.String(32), server_default=sa.text("'ocr'"), nullable=False),
        sa.Column("source_notice_id", sa.UUID(), nullable=True),
        sa.Column("ocr_confidence", sa.Numeric(3, 2), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "meal_slot IN ('lunch', 'snack_am', 'snack_pm')", name="daycare_meal_slot"
        ),
        sa.CheckConstraint("origin IN ('ocr', 'center_standard')", name="daycare_meal_origin"),
        sa.ForeignKeyConstraint(["child_id"], ["child.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "child_id", "serve_date", "meal_slot", name="uq_daycare_meal_child_date_slot"
        ),
    )


def downgrade() -> None:
    op.drop_table("daycare_meal")
