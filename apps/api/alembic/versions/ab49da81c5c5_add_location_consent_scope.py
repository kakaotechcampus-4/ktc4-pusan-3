"""consent_scope 에 location 을 더한다 (#172)

위치 동의는 가입 화면의 선택 항목이다. 받는 값은 보호자 휴대폰의 대략적인 위치라
아이가 아니라 **보호자 본인의 동의(계정 동의)** 다 — 그래서 값 목록만이 아니라
"계정 동의는 subject_parent_id 를 요구한다" 는 CHECK 의 계정 목록에도 넣는다.

값 목록이 VARCHAR + CHECK 로 들어가 있어(app/infra/db/types.py) 값 추가가 곧 제약 교체다.
같은 이름(consent_scope)의 CHECK 가 세 테이블에 있다 — consent · consent_retention ·
policy_version. 하나라도 빠지면 그 테이블만 location 을 거절한다.

🚨 downgrade 는 location 행이 없을 때만 된다. 있으면 옛 CHECK 를 다시 거는 데서 멈춘다 —
   위치 동의 증빙을 임의로 지우거나 다른 값으로 바꾸지 않고 시끄럽게 멈추는 것이 맞다.

Revision ID: ab49da81c5c5
Revises: e075ad671ec9
Create Date: 2026-09-29
"""

from typing import Sequence, Union

from alembic import op

revision: str = "ab49da81c5c5"
down_revision: Union[str, Sequence[str], None] = "e075ad671ec9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCOPE_CONSTRAINT = "consent_scope"
SCOPE_TABLES = ("consent", "consent_retention", "policy_version")

BEFORE = ("service_terms", "privacy_account", "child_basic", "child_health", "quality_improve")
AFTER = (*BEFORE, "location")

TARGET_CONSTRAINT = "ck_consent_target_matches_scope"
ACCOUNT_BEFORE = ("service_terms", "privacy_account")
ACCOUNT_AFTER = (*ACCOUNT_BEFORE, "location")


def _in(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def _target_matches_scope(account: tuple[str, ...]) -> str:
    """app/domains/consent/models.py 의 _TARGET_MATCHES_SCOPE 와 같은 식."""
    return (
        f"(scope IN ({_in(account)}) "
        "AND subject_parent_id IS NOT NULL AND child_id IS NULL) OR "
        f"(scope NOT IN ({_in(account)}) "
        "AND child_id IS NOT NULL AND subject_parent_id IS NULL)"
    )


def _swap(scopes: tuple[str, ...], account: tuple[str, ...]) -> None:
    for table in SCOPE_TABLES:
        op.drop_constraint(SCOPE_CONSTRAINT, table, type_="check")
        op.create_check_constraint(SCOPE_CONSTRAINT, table, f"scope IN ({_in(scopes)})")
    op.drop_constraint(TARGET_CONSTRAINT, "consent", type_="check")
    op.create_check_constraint(TARGET_CONSTRAINT, "consent", _target_matches_scope(account))


def upgrade() -> None:
    _swap(AFTER, ACCOUNT_AFTER)


def downgrade() -> None:
    _swap(BEFORE, ACCOUNT_BEFORE)
