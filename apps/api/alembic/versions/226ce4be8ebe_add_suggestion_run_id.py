"""suggestion 에 run_id 를 더한다

05(#249)가 "후보 보기 →" 로 연 run 의 추천을 찾는다. 추천을 저장하는 러너만 run 을 안다.
nullable — 이 칸이 생기기 전의 행과 run 밖에서 만든 행은 비어 있다.

Revision ID: 226ce4be8ebe
Revises: 5c09288aa40c
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "226ce4be8ebe"
down_revision: Union[str, Sequence[str], None] = "5c09288aa40c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("suggestion", sa.Column("run_id", sa.Text(), nullable=True))
    op.create_index("ix_suggestion_child_id_run_id", "suggestion", ["child_id", "run_id"])


def downgrade() -> None:
    op.drop_index("ix_suggestion_child_id_run_id", table_name="suggestion")
    op.drop_column("suggestion", "run_id")
