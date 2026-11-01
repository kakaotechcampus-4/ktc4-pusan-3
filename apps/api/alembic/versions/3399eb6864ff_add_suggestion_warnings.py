"""suggestion 에 warnings 를 더한다 (#402)

추천에 주의할 요소가 있으면 위험 용어 사전의 축 문구(예: 작은 부품 · 물놀이)를 그 추천의 칸으로
싣는다. 코드 문구라 모델이 쓰지 않고, 근거가 아니어서 suggestion_evidence 에 들어가지 않는다.
allergens · items 와 같은 모양(text[] · 기본 빈 배열)이라 이 칸 전의 행은 빈 배열이다.

Revision ID: 3399eb6864ff
Revises: 23b12754714a
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "3399eb6864ff"
down_revision: Union[str, Sequence[str], None] = "23b12754714a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "suggestion",
        sa.Column(
            "warnings",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default="{}",
        ),
    )


def downgrade() -> None:
    op.drop_column("suggestion", "warnings")
