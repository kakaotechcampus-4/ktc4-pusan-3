"""health_safety 에서 active 인 같은 항목(아이 · 종류 · 이름)을 두 줄 두지 않는다

등록 API 는 "이미 있나" 를 코드로 먼저 보지만, 두 보호자가 거의 동시에 누르면 둘 다 "없다" 를
보고 둘 다 저장한다. 그러면 하나를 내려도 하나가 남는다. 부분 고유 인덱스로 DB 가 두 번째를
막고, 라우터가 그 거절을 409 already_exists 로 돌려준다.

active 줄에만 건다 — 내린(retracted) 줄은 몇 개든 남을 수 있고, 같은 이름을 다시 등록할 수 있다.

Revision ID: d0be81909739
Revises: 988fc4ef647d
Create Date: 2026-10-13
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d0be81909739"
down_revision: Union[str, Sequence[str], None] = "988fc4ef647d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "uq_health_safety_active_item",
        "health_safety",
        ["child_id", "kind", "label"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )


def downgrade() -> None:
    op.drop_index("uq_health_safety_active_item", table_name="health_safety")
