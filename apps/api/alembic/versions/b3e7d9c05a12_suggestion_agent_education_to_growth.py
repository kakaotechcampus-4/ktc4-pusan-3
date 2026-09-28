"""suggestion_agent 의 education 을 growth 로 바꾼다

도메인 Agent 4종은 `food` / `activity` / `growth` / `health` 다 (루트 CLAUDE.md §5).
ORM 이 올라갈 때 §5 가 아직 `education` 이어서 그 값을 따랐고, §5 는 f9fc83b 에서 정정됐다.
Supervisor 쪽 DomainAgentName 은 처음부터 `growth` 라 두 enum 이 한 칸 어긋나 있었다.

`growth` 가 읽는 관찰 테이블은 `observation_education` 과 `observation_routine` 두 개다.
Agent 이름이 `education` 이면 둘 중 하나만 가리킨다. 테이블 이름은 그대로 둔다.

값 목록이 VARCHAR + CHECK 로 들어가 있어(app/infra/db/types.py) 값 교체가 곧 제약 교체다.
CHECK 를 먼저 떼고 행을 옮긴 뒤 다시 건다 — 순서를 바꾸면 UPDATE 가 옛 제약에 걸린다.

Revision ID: b3e7d9c05a12
Revises: 9c8d7e6f5a4b
Create Date: 2026-09-24
"""

from typing import Sequence, Union

from alembic import op

revision: str = "b3e7d9c05a12"
down_revision: Union[str, Sequence[str], None] = "9c8d7e6f5a4b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

CONSTRAINT = "suggestion_agent"
TABLE = "suggestion"

BEFORE = ("food", "activity", "education", "health")
AFTER = ("food", "activity", "growth", "health")


def _check(values: tuple[str, ...]) -> str:
    joined = ", ".join(f"'{value}'" for value in values)
    return f"agent IN ({joined})"


def _swap(values: tuple[str, ...], *, old: str, new: str) -> None:
    op.drop_constraint(CONSTRAINT, TABLE, type_="check")
    op.execute(f"UPDATE {TABLE} SET agent = '{new}' WHERE agent = '{old}'")
    op.create_check_constraint(CONSTRAINT, TABLE, _check(values))


def upgrade() -> None:
    _swap(AFTER, old="education", new="growth")


def downgrade() -> None:
    _swap(BEFORE, old="growth", new="education")
