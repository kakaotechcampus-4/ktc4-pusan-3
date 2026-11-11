"""푸시 기기를 로그인(session)에 묶고, 보호자별 알림 켜고 끄기 표를 만든다 (#594)

- push_device.session_id — 로그아웃 · 세션 만료 · 탈퇴로 세션이 지워지면 기기도 함께 지운다
  (CASCADE). 로그인 하나에 기기 하나라 유니크다.
- push_device.enabled 를 지운다. 켜고 끄기는 기기가 아니라 보호자 단위(notification_setting)다.
- push_platform 에 web 을 더한다 — 브라우저도 FCM 으로 받는다.
- notification_setting — 보호자당 1행, 일정 · 복약 스위치. 행이 없으면 코드의 기본값으로 본다.

session_id 는 NOT NULL 로 바로 더한다 — push_device 에는 아직 쓰기 경로가 없어 행이 없다.
행이 있으면 ADD COLUMN 이 실패한다(어느 로그인의 기기인지 알 수 없다).

Revision ID: d916c437f5dd
Revises: db405527cc82
Create Date: 2026-10-28
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d916c437f5dd"
down_revision: Union[str, Sequence[str], None] = "db405527cc82"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _platforms(*values: str) -> str:
    # 이름 · 식은 모델(enum_col_py(PushPlatform, name="push_platform"))이 만드는 CHECK 와 같다
    joined = ", ".join(f"'{v}'" for v in values)
    return f"platform IN ({joined})"


def upgrade() -> None:
    op.create_table(
        "notification_setting",
        sa.Column("parent_id", sa.UUID(), nullable=False),
        sa.Column("schedule_enabled", sa.Boolean(), nullable=False),
        sa.Column("medication_enabled", sa.Boolean(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["parent_id"], ["parent.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("parent_id"),
    )
    op.add_column("push_device", sa.Column("session_id", sa.UUID(), nullable=False))
    op.create_unique_constraint("push_device_session_id_key", "push_device", ["session_id"])
    op.create_foreign_key(
        "push_device_session_id_fkey",
        "push_device",
        "session",
        ["session_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.drop_column("push_device", "enabled")
    op.drop_constraint("push_platform", "push_device", type_="check")
    op.create_check_constraint("push_platform", "push_device", _platforms("ios", "android", "web"))


def downgrade() -> None:
    # web 기기가 있으면 CHECK 가 실패한다 — 지운 뒤 내린다
    op.execute("DELETE FROM push_device WHERE platform = 'web'")
    op.drop_constraint("push_platform", "push_device", type_="check")
    op.create_check_constraint("push_platform", "push_device", _platforms("ios", "android"))
    op.add_column(
        "push_device",
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
    )
    op.drop_constraint("push_device_session_id_fkey", "push_device", type_="foreignkey")
    op.drop_constraint("push_device_session_id_key", "push_device", type_="unique")
    op.drop_column("push_device", "session_id")
    op.drop_table("notification_setting")
