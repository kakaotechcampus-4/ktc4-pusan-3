"""보류 기록을 child_id + domain + observation_id 로 식별한다.

기존 관찰에서 child_id 를 채워 횟수, 해시와 타임스탬프를 보존한다.
아이 삭제는 DB CASCADE, 관찰 soft delete 는 repository 가 정리한다.

downgrade 는 관찰 FK 를 복원한다. 참조 관찰이 없는 행은 FK 검증에서 실패해
전체 트랜잭션을 롤백하며, 보류 기록을 임의로 삭제하지 않는다.

Revision ID: c37a8e1d902f
Revises: a2b3c4d5e6f7
"""

import sqlalchemy as sa
from alembic import op

revision = "c37a8e1d902f"
down_revision = "a2b3c4d5e6f7"
branch_labels = None
depends_on = None

TABLE = "observation_link_hold"
DOMAINS = ("food", "activity", "education")


def upgrade() -> None:
    op.execute(f"LOCK TABLE {TABLE} IN ACCESS EXCLUSIVE MODE")
    op.add_column(TABLE, sa.Column("child_id", sa.UUID(), nullable=True))
    op.add_column(TABLE, sa.Column("domain", sa.String(), nullable=True))
    op.add_column(TABLE, sa.Column("observation_id", sa.UUID(), nullable=True))
    for domain in DOMAINS:
        op.execute(
            sa.text(f"""
            UPDATE {TABLE} AS hold
            SET child_id = observation.child_id,
                domain = '{domain}', observation_id = observation.id
            FROM observation_{domain} AS observation
            WHERE hold.{domain}_id = observation.id
        """)
        )
    for column in ("child_id", "domain", "observation_id"):
        op.alter_column(TABLE, column, nullable=False)
    op.create_foreign_key(
        f"{TABLE}_child_id_fkey", TABLE, "child", ["child_id"], ["id"], ondelete="CASCADE"
    )
    op.create_index(f"ix_{TABLE}_child_id", TABLE, ["child_id"])
    op.create_unique_constraint(
        f"{TABLE}_domain_observation_key", TABLE, ["domain", "observation_id"]
    )
    op.create_check_constraint(
        f"{TABLE}_domain", TABLE, "domain IN ('food', 'activity', 'education')"
    )
    op.drop_constraint(f"{TABLE}_one_target", TABLE, type_="check")
    for domain in DOMAINS:
        op.drop_column(TABLE, f"{domain}_id")


def downgrade() -> None:
    op.execute(f"LOCK TABLE {TABLE} IN ACCESS EXCLUSIVE MODE")
    for domain in DOMAINS:
        column = f"{domain}_id"
        op.add_column(TABLE, sa.Column(column, sa.UUID(), nullable=True))
        op.execute(
            sa.text(f"UPDATE {TABLE} SET {column} = observation_id WHERE domain = '{domain}'")
        )
        op.create_foreign_key(
            f"{TABLE}_{column}_fkey",
            TABLE,
            f"observation_{domain}",
            [column],
            ["id"],
            ondelete="CASCADE",
        )
        op.create_unique_constraint(f"{TABLE}_{column}_key", TABLE, [column])
    op.create_check_constraint(
        f"{TABLE}_one_target", TABLE, "num_nonnulls(food_id, activity_id, education_id) = 1"
    )
    op.drop_constraint(f"{TABLE}_domain_observation_key", TABLE, type_="unique")
    op.drop_constraint(f"{TABLE}_domain", TABLE, type_="check")
    op.drop_index(f"ix_{TABLE}_child_id", table_name=TABLE)
    for column in ("child_id", "domain", "observation_id"):
        op.drop_column(TABLE, column)
