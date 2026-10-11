"""보호자당 아이 1명 제약 + 초대 코드를 해시로 저장한다 (#198).

parent_child: (parent_id, child_id) 유니크를 parent_id 유니크로 바꾼다. 앞의 것은 뒤의 것에
포함된다. 아이 등록(#92)과 초대 수락(#198)이 이 위반을 409 child_already_exists 로 바꾼다.
이미 아이가 둘 이상인 보호자가 있으면 upgrade 가 실패한다 — 행을 임의로 지우지 않는다.

invite: 원문 token 컬럼을 지우고 code_hash(SHA-256) 를 둔다. 초대 API 가 없어 지금까지 쓰인
행이 없으므로 값을 옮기지 않는다. 남은 행이 있으면 NOT NULL 추가에서 실패한다.

downgrade 는 parent_id 유니크를 되돌리고 token 컬럼을 다시 만든다. 해시에서 원문을
복원할 수 없으므로 invite 에 행이 있으면 실패한다.

Revision ID: 3e7a9c1d5b20
Revises: c37a8e1d902f
"""

import sqlalchemy as sa
from alembic import op

revision = "3e7a9c1d5b20"
down_revision = "c37a8e1d902f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("parent_child_parent_id_child_id_key", "parent_child", type_="unique")
    op.create_unique_constraint("uq_parent_child_parent_id", "parent_child", ["parent_id"])

    op.drop_constraint("invite_token_key", "invite", type_="unique")
    op.drop_column("invite", "token")
    op.add_column("invite", sa.Column("code_hash", sa.LargeBinary(), nullable=False))
    # 수락 · 확인이 code_hash 로 한 행을 집는다.
    op.create_index("ix_invite_code_hash", "invite", ["code_hash"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_invite_code_hash", table_name="invite")
    op.drop_column("invite", "code_hash")
    op.add_column("invite", sa.Column("token", sa.Text(), nullable=False))
    op.create_unique_constraint("invite_token_key", "invite", ["token"])

    op.drop_constraint("uq_parent_child_parent_id", "parent_child", type_="unique")
    op.create_unique_constraint(
        "parent_child_parent_id_child_id_key", "parent_child", ["parent_id", "child_id"]
    )
