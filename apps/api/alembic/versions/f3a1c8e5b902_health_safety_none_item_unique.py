"""health_safety 에서 괜찮았던 식품(none)인 같은 항목(아이 · 종류 · 이름)을 두 줄 두지 않는다

05 알레르기 확인 시트가 "먹어봤어요 · 괜찮았어요" 를 status = none 으로 저장하게 되면서(등록 API 의
tolerated), active 와 같은 이유로 부분 고유 인덱스를 건다 — 두 보호자가 거의 동시에 저장해도 두 줄이
되지 않고, 라우터가 그 거절을 409 already_exists 로 돌려준다.

none 줄에만 건다 — 내린(retracted) 줄은 몇 개든 남을 수 있고, 같은 이름을 다시 저장할 수 있다.
지금까지 none 을 쓰는 길이 없어서 기존 행과 부딪히지 않는다. 인덱스 추가라 expand-only 다.

Revision ID: f3a1c8e5b902
Revises: 2286e36180b9
Create Date: 2026-11-09
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f3a1c8e5b902"
down_revision: Union[str, Sequence[str], None] = "2286e36180b9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "uq_health_safety_none_item",
        "health_safety",
        ["child_id", "kind", "label"],
        unique=True,
        postgresql_where=sa.text("status = 'none'"),
    )


def downgrade() -> None:
    op.drop_index("uq_health_safety_none_item", table_name="health_safety")
