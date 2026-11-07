"""medication_dose 에 체크한 보호자(checked_by)를 더한다 (#749)

다른 보호자도 누가 언제 먹였는지 보게 한다. 결정과 근거는 docs/medication/medication-tables-v1.md.

- nullable — 이 칸이 생기기 전의 체크는 누른 보호자를 알 수 없어 비어 있다. 채우지 않는다.
- `parent` FK `ON DELETE SET NULL` — 탈퇴하면 비우고 복용 기록은 남긴다(공동 기록의 "적은 사람" 과 같다).
- CHECK — 체크하지 않은 회차(`checked_at IS NULL`)에 보호자만 남지 않게 한다.
- 인덱스 — 탈퇴 때 SET NULL 이 이 칸으로 행을 찾는다.

Revision ID: 361f26c96b2e
Revises: d5e8a1c4b937
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "361f26c96b2e"
down_revision: Union[str, Sequence[str], None] = "d5e8a1c4b937"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "medication_dose",
        sa.Column("checked_by", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "medication_dose_checked_by_fkey",
        "medication_dose",
        "parent",
        ["checked_by"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_check_constraint(
        "medication_dose_checked_by",
        "medication_dose",
        "checked_by IS NULL OR checked_at IS NOT NULL",
    )
    op.create_index("medication_dose_checked_by_idx", "medication_dose", ["checked_by"])


def downgrade() -> None:
    op.drop_index("medication_dose_checked_by_idx", table_name="medication_dose")
    op.drop_constraint("medication_dose_checked_by", "medication_dose", type_="check")
    op.drop_constraint("medication_dose_checked_by_fkey", "medication_dose", type_="foreignkey")
    op.drop_column("medication_dose", "checked_by")
