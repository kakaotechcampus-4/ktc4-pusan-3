"""Split consent actor from subject and add consent_retention.

Revision ID: b8d3e5a91c47
Revises: a4f9c2e17b83

동의의 행위자(actor)와 대상(subject)을 나눈다 — 노션 정책 정본 §5 · §9. 행위자가
탈퇴해도 아동 동의는 살아 있어야 하므로 actor 쪽만 SET NULL 이고, 대상 쪽은 RESTRICT 라
증빙을 옮기지 않은 삭제를 DB 가 막는다.

🚨 기존 consent 행이 없다는 전제로 컬럼을 갈아끼운다. 배포 전이고 개발 DB 도 0행임을
   확인했다. 행이 있는 DB 에서는 actor_ref · policy_version_id 의 NOT NULL 때문에
   upgrade 가 실패한다 — 그게 맞는 동작이다. parent_id 하나를 actor 로 볼지 subject 로
   볼지, policy_version 문자열을 어느 policy_version row 에 붙일지는 추측할 수 없어서,
   임의의 기본값으로 채우는 대신 시끄럽게 멈춘다.

🚨 downgrade 는 구조만 되돌린다. 복원할 수 없는 것 둘.
     · actor_ref 와 actor/subject 의 구분 — 옛 스키마에는 parent_id 한 칸뿐이라
       두 값을 한 칸에 담을 수 없다.
     · consent_retention 의 증빙 — 대응하는 옛 테이블이 없다. 테이블째 사라진다.
   그래서 downgrade 도 행이 없을 때만 안전하다. 행이 있으면 parent_id 의 NOT NULL 에서
   멈춘다.
"""

import sqlalchemy as sa
from alembic import op

revision = "b8d3e5a91c47"
down_revision = "a4f9c2e17b83"
branch_labels = None
depends_on = None

CONSENT_SCOPE = sa.Enum(
    "service_terms",
    "privacy_account",
    "child_basic",
    "child_health",
    "quality_improve",
    name="consent_scope",
    native_enum=False,
    create_constraint=True,
    length=32,
)
CONSENT_ACTION = sa.Enum(
    "granted",
    "withdrawn",
    name="consent_action",
    native_enum=False,
    create_constraint=True,
    length=32,
)

_ACCOUNT_SCOPES = "'service_terms','privacy_account'"
TARGET_MATCHES_SCOPE = (
    f"(scope IN ({_ACCOUNT_SCOPES}) "
    "AND subject_parent_id IS NOT NULL AND child_id IS NULL) OR "
    f"(scope NOT IN ({_ACCOUNT_SCOPES}) "
    "AND child_id IS NOT NULL AND subject_parent_id IS NULL)"
)

OLD_SCOPE_CHILD_ID = (
    f"(scope IN ({_ACCOUNT_SCOPES}) AND child_id IS NULL) OR "
    f"(scope NOT IN ({_ACCOUNT_SCOPES}) AND child_id IS NOT NULL)"
)


def upgrade() -> None:
    op.drop_constraint("ck_consent_scope_child_id", "consent", type_="check")
    op.drop_constraint("consent_parent_id_fkey", "consent", type_="foreignkey")
    op.drop_column("consent", "parent_id")
    op.drop_column("consent", "policy_version")

    op.add_column("consent", sa.Column("subject_parent_id", sa.UUID(), nullable=True))
    op.add_column("consent", sa.Column("actor_parent_id", sa.UUID(), nullable=True))
    op.add_column("consent", sa.Column("actor_ref", sa.UUID(), nullable=False))
    op.add_column("consent", sa.Column("policy_version_id", sa.UUID(), nullable=False))

    op.create_foreign_key(
        "consent_subject_parent_id_fkey",
        "consent",
        "parent",
        ["subject_parent_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "consent_actor_parent_id_fkey",
        "consent",
        "parent",
        ["actor_parent_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "consent_policy_version_id_fkey",
        "consent",
        "policy_version",
        ["policy_version_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    # 기존 child_id FK 는 ondelete 가 없어 NO ACTION 이었다. 동작은 RESTRICT 와 같지만
    # (둘 다 지연 불가) pg_constraint 에 남는 값이 'a' 라서 "일부러 고른 값" 인지
    # "안 적은 값" 인지 구분되지 않는다. 막는 것이 의도이므로 RESTRICT 로 다시 건다.
    op.drop_constraint("consent_child_id_fkey", "consent", type_="foreignkey")
    op.create_foreign_key(
        "consent_child_id_fkey",
        "consent",
        "child",
        ["child_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint("ck_consent_target_matches_scope", "consent", TARGET_MATCHES_SCOPE)

    op.create_table(
        "consent_retention",
        sa.Column("id", sa.UUID(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("source_consent_id", sa.UUID(), nullable=False),
        sa.Column("actor_ref", sa.UUID(), nullable=False),
        sa.Column("subject_parent_ref", sa.UUID(), nullable=True),
        sa.Column("child_ref", sa.UUID(), nullable=True),
        sa.Column("scope", CONSENT_SCOPE, nullable=False),
        sa.Column("action", CONSENT_ACTION, nullable=False),
        sa.Column("policy_version_id", sa.UUID(), nullable=False),
        sa.Column("guardian_attested", sa.Boolean(), nullable=True),
        sa.Column("acted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("retained_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("purge_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["policy_version_id"], ["policy_version.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("consent_retention")

    op.drop_constraint("ck_consent_target_matches_scope", "consent", type_="check")
    op.drop_constraint("consent_policy_version_id_fkey", "consent", type_="foreignkey")
    op.drop_constraint("consent_actor_parent_id_fkey", "consent", type_="foreignkey")
    op.drop_constraint("consent_subject_parent_id_fkey", "consent", type_="foreignkey")
    op.drop_column("consent", "policy_version_id")
    op.drop_column("consent", "actor_ref")
    op.drop_column("consent", "actor_parent_id")
    op.drop_column("consent", "subject_parent_id")

    op.add_column("consent", sa.Column("policy_version", sa.Text(), nullable=False))
    op.add_column("consent", sa.Column("parent_id", sa.UUID(), nullable=False))
    op.create_foreign_key("consent_parent_id_fkey", "consent", "parent", ["parent_id"], ["id"])
    op.create_check_constraint("ck_consent_scope_child_id", "consent", OLD_SCOPE_CHILD_ID)
