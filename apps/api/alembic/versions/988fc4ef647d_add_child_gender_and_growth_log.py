"""아이 성별 칸과 키·몸무게 기록 표를 더한다 (C 묶음)

- child.gender — male · female · undisclosed. 이미 있는 아이는 전부 undisclosed 가 된다.
  기본값이 "밝히지 않을래요" 인 것은 루트 CLAUDE.md §2 다.
- child_growth_log — 키 · 몸무게를 잰 날 한 줄. 둘 다 빈 행과 0 이하 값은 DB 가 막는다.
  칸 이름은 Agent 포트(agents/food/store/ports.py 의 Measurement)와 같고, 자릿수(numeric(4,1))는
  docs/agents/data_model.md 의 09-22 확정값과 같다.
  아이가 지워지면 같이 지워지고, 적은 보호자가 탈퇴하면 적은 사람만 비운다 (공동 기록).

Revision ID: 988fc4ef647d
Revises: d8e9f0a1b2c3
Create Date: 2026-10-10
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "988fc4ef647d"
down_revision: Union[str, Sequence[str], None] = "d8e9f0a1b2c3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

GENDER_VALUES = ("male", "female", "undisclosed")


def upgrade() -> None:
    # 이름 · 식은 모델(enum_col_py(Gender, name="child_gender"))이 만드는 CHECK 와 같다
    op.add_column(
        "child",
        sa.Column(
            "gender", sa.String(32), server_default=sa.text("'undisclosed'"), nullable=False
        ),
    )
    values = ", ".join(f"'{value}'" for value in GENDER_VALUES)
    op.create_check_constraint("child_gender", "child", f"gender IN ({values})")

    op.create_table(
        "child_growth_log",
        sa.Column("id", sa.UUID(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("child_id", sa.UUID(), nullable=False),
        sa.Column("measured_on", sa.Date(), nullable=False),
        sa.Column("height_cm", sa.Numeric(4, 1), nullable=True),
        sa.Column("weight_kg", sa.Numeric(4, 1), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_by", sa.UUID(), nullable=True),
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
            "height_cm IS NOT NULL OR weight_kg IS NOT NULL", name="child_growth_log_measured"
        ),
        sa.CheckConstraint(
            "height_cm IS NULL OR height_cm > 0", name="child_growth_log_height_positive"
        ),
        sa.CheckConstraint(
            "weight_kg IS NULL OR weight_kg > 0", name="child_growth_log_weight_positive"
        ),
        sa.ForeignKeyConstraint(["child_id"], ["child.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["parent.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_child_growth_log_child_id_measured_on",
        "child_growth_log",
        ["child_id", "measured_on"],
    )


def downgrade() -> None:
    op.drop_index("ix_child_growth_log_child_id_measured_on", table_name="child_growth_log")
    op.drop_table("child_growth_log")
    op.drop_constraint("child_gender", "child", type_="check")
    op.drop_column("child", "gender")
