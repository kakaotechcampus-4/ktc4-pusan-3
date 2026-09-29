"""observation_link_hold 테이블을 만든다

Curator 연결 단계에서 판정기가 "모르겠음(uncertain)"이라 답해 보류된 횟수를 관찰마다 센다.
같은 관찰이 3번 보류되면 새 candidate Profile 을 만든다 (PR #163 회의 결정). 관찰 테이블에
칸을 더하지 않고 따로 둔다 — 관찰 테이블과 그 저장소 코드를 건드리지 않기 위해서다.

관찰이 도메인마다 테이블이 달라서 FK 칸을 셋(food · activity · education) 두고 하나만 채운다.
`correction.target_id` · `suggestion_evidence.source_id` 처럼 FK 없이 두지 않는 이유는 삭제다.
이 행에는 subject 해시가 있어서 관찰이 지워지면 같이 지워져야 하고, 그것을 DB 가 보장한다.

세는 것은 uncertain 뿐이다. 오류 · 목록 밖의 답은 세지 않는다. 판정 모델 · confidence 처럼
판정 품질을 보는 값은 두지 않는다 — 처리방침 8장(품질 평가에 쓰지 않는다)과 맞춘다.

CHECK 두 개는 autogenerate 가 잡지 않아 손으로 넣었다 (apps/api/CLAUDE.md §Alembic).

Revision ID: b901cf42a99f
Revises: bf14612a1d9c
Create Date: 2026-09-30
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b901cf42a99f"
down_revision: Union[str, Sequence[str], None] = "bf14612a1d9c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "observation_link_hold"
TARGETS = {
    "food_id": "observation_food",
    "activity_id": "observation_activity",
    "education_id": "observation_education",
}


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", sa.UUID(), server_default=sa.text("uuidv7()"), nullable=False),
        *(sa.Column(column, sa.UUID(), nullable=True) for column in TARGETS),
        sa.Column("uncertain_count", sa.SmallInteger(), nullable=False),
        sa.Column("subject_hash", sa.Text(), nullable=False),
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
        *(
            sa.ForeignKeyConstraint([column], [f"{table}.id"], ondelete="CASCADE")
            for column, table in TARGETS.items()
        ),
        *(sa.UniqueConstraint(column) for column in TARGETS),
        sa.CheckConstraint(
            "num_nonnulls(food_id, activity_id, education_id) = 1",
            name="observation_link_hold_one_target",
        ),
        sa.CheckConstraint(
            "uncertain_count BETWEEN 1 AND 3", name="observation_link_hold_uncertain_count"
        ),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table(TABLE)
