"""suggestion 에 place_notice 를 더한다 (#516)

장소 이름이 붙은 추천에 코드가 만든 확인 문구(공공데이터 기준일 · 운영 여부 확인)를 싣는다.
문장(content)에 섞지 않고 칸으로 두어 화면이 제안 문장 아래 한 줄로 세운다(FE 합의).
nullable — 장소가 없는 추천과 이 칸이 생기기 전의 행은 비어 있다.

Revision ID: 01803571133f
Revises: 5bdc0aad2e70
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "01803571133f"
down_revision: Union[str, Sequence[str], None] = "5bdc0aad2e70"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("suggestion", sa.Column("place_notice", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("suggestion", "place_notice")
