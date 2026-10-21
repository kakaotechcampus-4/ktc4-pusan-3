"""복약 코스 · 회차 두 표를 더한다 (#537)

이슈 #537 본문의 DDL 에 코멘트 결정을 얹었다. 결정과 근거는 docs/medication/medication-tables-v1.md.

- `medication_schedule.status` 는 active / stopped 둘이다. 끝남은 `ends_on` 으로 계산한다.
- `medication_dose` 는 회차 1번 = 1행이고 created_at · updated_at 이 없다.
- `source_draft_id` 는 FK 없이 둔다 — prescription_draft 가 생길 때 단다.
- enum 칸은 VARCHAR(32) + CHECK (app/infra/db/types.py 의 enum_col_py 와 같은 모양).

🚨 `scheduled_time` 생성 컬럼과 CHECK 는 손으로 확인했다. autogenerate 는 새 표를 만들 때만
   모델에서 옮겨 적고, 이미 있는 표의 생성 식 · CHECK 변경은 비교하지 않는다. 기준 시각을 바꾸면
   STORED 컬럼이라 표를 다시 쓰는 마이그레이션을 따로 써야 한다.

Revision ID: c6e2a8d41f93
Revises: 091b165cedea
Create Date: 2026-10-21
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c6e2a8d41f93"
down_revision: Union[str, Sequence[str], None] = "091b165cedea"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 모델(app/domains/medication/models.py)의 SCHEDULED_TIME_SQL 과 같은 글자여야 한다.
# 마이그레이션은 앱 코드를 import 하지 않는다 — 나중에 모델이 바뀌어도 이 revision 은 그대로다.
SCHEDULED_TIME_SQL = """CASE timing_anchor
    WHEN 'fixed' THEN fixed_time
    WHEN 'wake_up' THEN TIME '07:30' + make_interval(mins => offset_minutes)
    WHEN 'breakfast' THEN TIME '08:00' + make_interval(mins => offset_minutes)
    WHEN 'lunch' THEN TIME '12:00' + make_interval(mins => offset_minutes)
    WHEN 'dinner' THEN TIME '17:00' + make_interval(mins => offset_minutes)
    WHEN 'bedtime' THEN TIME '20:30' + make_interval(mins => offset_minutes)
END"""


def _enum(*values: str, name: str) -> sa.Enum:
    return sa.Enum(*values, name=name, native_enum=False, create_constraint=True, length=32)


def upgrade() -> None:
    op.create_table(
        "medication_schedule",
        sa.Column("id", sa.UUID(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("child_id", sa.UUID(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("dosage", sa.Text(), nullable=True),
        sa.Column("indication_text", sa.Text(), nullable=True),
        sa.Column(
            "symptom_codes", postgresql.ARRAY(sa.Text()), server_default="{}", nullable=False
        ),
        sa.Column("storage", _enum("room", "fridge", name="medication_storage"), nullable=True),
        sa.Column(
            "med_form",
            _enum("liquid", "powder", "tablet", "ointment", "drops", name="medication_form"),
            nullable=True,
        ),
        sa.Column("starts_on", sa.Date(), nullable=False),
        sa.Column("ends_on", sa.Date(), nullable=False),
        sa.Column(
            "status",
            _enum("active", "stopped", name="medication_status"),
            server_default="active",
            nullable=False,
        ),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "created_by", _enum("agent", "caregiver", name="medication_created_by"), nullable=False
        ),
        sa.Column(
            "source",
            _enum("utterance", "prescription_ocr", name="medication_source"),
            server_default="utterance",
            nullable=False,
        ),
        sa.Column("source_draft_id", sa.UUID(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.CheckConstraint("ends_on >= starts_on", name="medication_schedule_period"),
        sa.ForeignKeyConstraint(["child_id"], ["child.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "medication_schedule_child_idx", "medication_schedule", ["child_id", "status"]
    )

    op.create_table(
        "medication_dose",
        sa.Column("id", sa.UUID(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("schedule_id", sa.UUID(), nullable=False),
        sa.Column("dose_date", sa.Date(), nullable=False),
        sa.Column(
            "timing_anchor",
            _enum(
                "fixed",
                "wake_up",
                "breakfast",
                "lunch",
                "dinner",
                "bedtime",
                name="medication_timing_anchor",
            ),
            nullable=False,
        ),
        sa.Column(
            "offset_minutes", sa.SmallInteger(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("fixed_time", sa.Time(), nullable=True),
        sa.Column(
            "scheduled_time",
            sa.Time(),
            sa.Computed(SCHEDULED_TIME_SQL, persisted=True),
            nullable=True,
        ),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "(timing_anchor = 'fixed') = (fixed_time IS NOT NULL)",
            name="medication_dose_fixed_time",
        ),
        sa.CheckConstraint(
            "timing_anchor <> 'fixed' OR offset_minutes = 0", name="medication_dose_fixed_offset"
        ),
        sa.CheckConstraint(
            "offset_minutes BETWEEN -120 AND 180", name="medication_dose_offset_range"
        ),
        sa.ForeignKeyConstraint(["schedule_id"], ["medication_schedule.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("schedule_id", "dose_date", "scheduled_time"),
    )


def downgrade() -> None:
    op.drop_table("medication_dose")
    op.drop_index("medication_schedule_child_idx", table_name="medication_schedule")
    op.drop_table("medication_schedule")
