"""Add policy_version table with placeholder seed content.

Revision ID: a4f9c2e17b83
Revises: e7b2a9c4f013

정책 본문(policy_version.content)은 아직 확정되지 않았다 — 실제 이용약관·개인정보
처리방침 문구는 이 저장소 어디에도 없고 PM 결정 대상이다(Issue #47 PR B). 검증 로직이
동작하려면 시드 데이터가 필요하므로, 4개 scope(service_terms / privacy_account /
child_basic / child_health)를 **명시적인 placeholder content**로 등록한다.

🚨 이 마이그레이션의 content 는 placeholder 다. 실제 약관·처리방침이 확정되면 새
policy_version row(같은 scope, 새 version)를 추가해 교체한다 — 기존 row 는 수정하지
않는다 (policy_version 은 immutable, app/domains/policy/models.py 참고).
"""

import hashlib
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision = "a4f9c2e17b83"
down_revision = "e7b2a9c4f013"
branch_labels = None
depends_on = None

PLACEHOLDER_CONTENT = "TODO: 실제 약관 본문 미확정 — PM 승인 후 이 placeholder를 교체할 것"
PLACEHOLDER_VERSION = "draft-0"
# 마이그레이션 적용 시점이 아니라 고정된 상수 — Alembic 스크립트는 실행 시각에 의존하지
# 않아야 재적용·리플레이 결과가 항상 같다.
PLACEHOLDER_EFFECTIVE_AT = datetime(2026, 1, 1, tzinfo=UTC)
SEEDED_SCOPES = ("service_terms", "privacy_account", "child_basic", "child_health")

policy_version = sa.table(
    "policy_version",
    sa.column("scope", sa.String),
    sa.column("version", sa.String),
    sa.column("content", sa.Text),
    sa.column("content_hash", sa.String),
    sa.column("effective_at", sa.DateTime),
)


def upgrade() -> None:
    op.create_table(
        "policy_version",
        sa.Column("id", sa.UUID(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column(
            "scope",
            sa.Enum(
                "service_terms",
                "privacy_account",
                "child_basic",
                "child_health",
                "quality_improve",
                name="consent_scope",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("version", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.UniqueConstraint("scope", "version", name="uq_policy_version_scope_version"),
        sa.PrimaryKeyConstraint("id"),
    )

    content_hash = hashlib.sha256(PLACEHOLDER_CONTENT.encode("utf-8")).hexdigest()
    op.bulk_insert(
        policy_version,
        [
            {
                "scope": scope,
                "version": PLACEHOLDER_VERSION,
                "content": PLACEHOLDER_CONTENT,
                "content_hash": content_hash,
                "effective_at": PLACEHOLDER_EFFECTIVE_AT,
            }
            for scope in SEEDED_SCOPES
        ],
    )


def downgrade() -> None:
    op.drop_table("policy_version")
