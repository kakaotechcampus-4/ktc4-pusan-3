"""기관 공지 원문 notice 와 거기서 뽑은 항목 notice_item 을 만든다

공지 한 장에는 행사 · 회신 마감 · 비용 · 운영 변경이 섞여 온다. 공지 단위로 한 갈래만 고르면 나머지가
사라져서, 원문 1행에 항목 N행을 매단다.

그동안 FK 없이 uuid 로 있던 source_notice_id 7곳을 notice 에 잇는다. NOT VALID 로 거는 이유 —
기존 행을 검사하지 않아서 이미 값이 든 행이 있어도 배포가 멈추지 않고, 새로 쓰는 값은 검사한다.

Revision ID: b7c41d20a9e3
Revises: 4d4f6a557b41
Create Date: 2026-11-01
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b7c41d20a9e3"
down_revision: Union[str, Sequence[str], None] = "4d4f6a557b41"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SOURCE_NOTICE_TABLES = (
    "observation_food",
    "observation_education",
    "observation_activity",
    "observation_routine",
    "observation_health",
    "event",
    "daycare_meal",
)


def _enum(name: str, *values: str) -> sa.Enum:
    # CHECK 이름 · 식은 모델(enum_col_py)이 만드는 것과 같다
    return sa.Enum(*values, name=name, native_enum=False, length=32, create_constraint=True)


def upgrade() -> None:
    op.create_table(
        "notice",
        sa.Column("id", sa.UUID(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("child_id", sa.UUID(), nullable=False),
        sa.Column("uploader_parent_id", sa.UUID(), nullable=True),
        sa.Column(
            "doc_kind",
            _enum(
                "notice_doc_kind",
                "daily_note",
                "newsletter",
                "meal_plan",
                "schedule",
                "message",
                "other",
            ),
            nullable=False,
        ),
        sa.Column(
            "input_format",
            _enum("notice_input_format", "image", "pdf", "hwp", "hwpx", "xlsx", "text"),
            nullable=False,
        ),
        sa.Column(
            "audience_scope",
            _enum("notice_audience_scope", "child", "class", "institution", "unknown"),
            server_default="unknown",
            nullable=False,
        ),
        sa.Column("issued_on", sa.Date(), nullable=True),
        sa.Column(
            "received_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("raw_text", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column("related_notice_id", sa.UUID(), nullable=True),
        sa.Column("relation", _enum("notice_relation", "correction", "follow_up"), nullable=True),
        sa.Column("extractor_version", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "related_notice_id IS NULL OR relation IS NOT NULL",
            name="ck_notice_related_needs_relation",
        ),
        sa.ForeignKeyConstraint(["child_id"], ["child.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["related_notice_id"], ["notice.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["uploader_parent_id"], ["parent.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("child_id", "content_hash", name="uq_notice_child_content_hash"),
    )
    op.create_index("ix_notice_child_received_at", "notice", ["child_id", "received_at"])

    op.create_table(
        "notice_item",
        sa.Column("id", sa.UUID(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("notice_id", sa.UUID(), nullable=False),
        sa.Column(
            "handling", _enum("notice_item_handling", "calendar", "todo", "info"), nullable=False
        ),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column(
            "target_scope",
            _enum("notice_item_target", "child", "class", "all", "conditional", "unknown"),
            server_default="unknown",
            nullable=False,
        ),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("all_day", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("due_all_day", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "details",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "supplies",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column("source_quote", sa.Text(), nullable=False),
        sa.Column("done_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "starts_at IS NULL OR ends_at IS NULL OR ends_at >= starts_at",
            name="ck_notice_item_range",
        ),
        sa.ForeignKeyConstraint(
            ["notice_id"], ["notice.id"], ondelete="CASCADE", onupdate="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_notice_item_notice_id", "notice_item", ["notice_id"])
    op.create_index(
        "ix_notice_item_open_due",
        "notice_item",
        ["due_at"],
        postgresql_where=sa.text("due_at IS NOT NULL AND done_at IS NULL"),
    )

    # 공지 항목 → 일정은 1:1 이다. 일정 쪽이 항목을 가리킨다 — 항목이 먼저 생기고 일정은 승인 뒤에만 생긴다
    op.add_column("event", sa.Column("source_notice_item_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        "event_source_notice_item_id_fkey",
        "event",
        "notice_item",
        ["source_notice_item_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_unique_constraint(
        "uq_event_source_notice_item_id", "event", ["source_notice_item_id"]
    )

    for table in SOURCE_NOTICE_TABLES:
        op.create_foreign_key(
            f"{table}_source_notice_id_fkey",
            table,
            "notice",
            ["source_notice_id"],
            ["id"],
            ondelete="SET NULL",
            postgresql_not_valid=True,
        )


def downgrade() -> None:
    for table in SOURCE_NOTICE_TABLES:
        op.drop_constraint(f"{table}_source_notice_id_fkey", table, type_="foreignkey")
    op.drop_constraint("uq_event_source_notice_item_id", "event", type_="unique")
    op.drop_constraint("event_source_notice_item_id_fkey", "event", type_="foreignkey")
    op.drop_column("event", "source_notice_item_id")
    op.drop_index("ix_notice_item_open_due", table_name="notice_item")
    op.drop_index("ix_notice_item_notice_id", table_name="notice_item")
    op.drop_table("notice_item")
    op.drop_index("ix_notice_child_received_at", table_name="notice")
    op.drop_table("notice")
