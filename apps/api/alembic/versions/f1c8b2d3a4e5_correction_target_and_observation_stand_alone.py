"""Observation stand_alone 상태와 Correction 대상(target_kind + target_id)

correction 은 ALTER 대신 drop -> create 로 다시 만든다. affinity_id 하나뿐이던
대상 표현이 child_id / target_kind / target_id 로 통째로 바뀌고, 아직 저장된 행이
없어서 옮길 데이터가 없다. 컬럼 단위로 쪼개 적으면 읽는 쪽이 최종 형태를 알 수 없다.

observation_status 는 CHECK 를 교체한다. 값 목록이 VARCHAR + CHECK 로 들어가 있어
(app/infra/db/types.py 참고) 값 추가가 곧 제약 교체다.

Revision ID: f1c8b2d3a4e5
Revises: b8d3e5a91c47
Create Date: 2026-09-17
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f1c8b2d3a4e5"
down_revision: Union[str, Sequence[str], None] = "b8d3e5a91c47"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

OBSERVATION_TABLES = (
    "observation_food",
    "observation_health",
    "observation_education",
    "observation_activity",
)
STATUS_BEFORE = ("active", "inactive")
STATUS_AFTER = ("active", "stand_alone", "inactive")

TARGET_KINDS = (
    "observation_food",
    "observation_health",
    "observation_education",
    "observation_activity",
    "observation_routine",
    "profile_affinity",
)
VERDICTS = ("once_only", "wrong", "need_more_observation", "outdated")

# 대상별 허용 verdict. app/domains/correction/models.py 의 TARGET_VERDICT_CHECK 와 같은 문장이다
TARGET_VERDICT_CHECK = (
    "(target_kind IN ('observation_activity', 'observation_education', 'observation_food',"
    " 'observation_health', 'observation_routine')"
    " AND verdict IN ('once_only', 'wrong'))"
    " OR (target_kind = 'profile_affinity'"
    " AND verdict IN ('need_more_observation', 'outdated', 'wrong'))"
)


def _status_check(values: tuple[str, ...]) -> str:
    joined = ", ".join(f"'{v}'" for v in values)
    return f"status IN ({joined})"


def _replace_status_check(values: tuple[str, ...]) -> None:
    for table in OBSERVATION_TABLES:
        op.drop_constraint("observation_status", table, type_="check")
        op.create_check_constraint("observation_status", table, _status_check(values))


def _enum(*values: str, name: str) -> sa.Enum:
    return sa.Enum(*values, name=name, native_enum=False, create_constraint=True, length=32)


def upgrade() -> None:
    _replace_status_check(STATUS_AFTER)

    op.drop_table("correction")
    op.create_table(
        "correction",
        sa.Column("id", sa.UUID(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("child_id", sa.UUID(), nullable=False),
        sa.Column(
            "target_kind",
            _enum(*TARGET_KINDS, name="correction_target_kind"),
            nullable=False,
        ),
        sa.Column("target_id", sa.UUID(), nullable=False),
        sa.Column("verdict", _enum(*VERDICTS, name="correction_verdict"), nullable=False),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(TARGET_VERDICT_CHECK, name="correction_target_verdict"),
        sa.ForeignKeyConstraint(["child_id"], ["child.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["parent.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_correction_target", "correction", ["target_kind", "target_id"])
    op.create_index("ix_correction_child_id", "correction", ["child_id"])


def downgrade() -> None:
    op.drop_index("ix_correction_child_id", table_name="correction")
    op.drop_index("ix_correction_target", table_name="correction")
    op.drop_table("correction")
    op.create_table(
        "correction",
        sa.Column("id", sa.UUID(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("affinity_id", sa.UUID(), nullable=False),
        sa.Column(
            "verdict",
            _enum("confirm", "once_only", "outdated", "wrong", name="correction_verdict"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["affinity_id"], ["profile_affinity.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    _replace_status_check(STATUS_BEFORE)
