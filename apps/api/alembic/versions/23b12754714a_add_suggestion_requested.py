"""suggestion 에 requested 를 더한다 (#597 리뷰)

보호자가 놀이를 정해 물은 Activity 일반 추천("물놀이 뭐 하지?")은 그 요청이 기준이라 문서 행 근거
없이 저장된다(#599). 05 가 이 추천과 "문서 근거가 검수에서 빠진 일반 추천" 을 가르려면 저장할 때
요청 기반이었는지를 행에 남겨야 한다.

NOT NULL · 기본 false — 이 칸이 생기기 전의 행은 요청 기반 추천을 낸 적이 없다(Activity 가 아직
requested 를 보내지 않았다).

Revision ID: 23b12754714a
Revises: 01803571133f
Create Date: 2026-11-01
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "23b12754714a"
down_revision: Union[str, Sequence[str], None] = "01803571133f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "suggestion",
        sa.Column("requested", sa.Boolean(), server_default=sa.false(), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("suggestion", "requested")
