"""Observation status 에 deleted 추가

보호자의 삭제 요청은 관찰 행을 지우지 않고 status 를 deleted 로 바꾼다.
값 목록이 VARCHAR + CHECK 로 들어가 있어(app/infra/db/types.py 참고) 값 추가가 곧 제약 교체다.
f1c8b2d3a4e5 는 routine 이 생기기 전이라 4테이블이었고, 이번엔 observation_routine 까지 5개다.

downgrade 는 deleted 행을 hard delete 한 뒤 CHECK 를 되돌린다. inactive 로 내리면
보호자가 지운 기록이 "잘못된 기록" 목록에 다시 나타난다.

Revision ID: a7e3c91d5f20
Revises: c5a81f0d3b62
Create Date: 2026-09-29
"""

from typing import Sequence, Union

from alembic import op

revision: str = "a7e3c91d5f20"
down_revision: Union[str, Sequence[str], None] = "c5a81f0d3b62"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

OBSERVATION_TABLES = (
    "observation_food",
    "observation_health",
    "observation_education",
    "observation_activity",
    "observation_routine",
)
STATUS_BEFORE = ("active", "stand_alone", "inactive")
STATUS_AFTER = ("active", "stand_alone", "inactive", "deleted")


def _status_check(values: tuple[str, ...]) -> str:
    joined = ", ".join(f"'{v}'" for v in values)
    return f"status IN ({joined})"


def _replace_status_check(values: tuple[str, ...]) -> None:
    for table in OBSERVATION_TABLES:
        op.drop_constraint("observation_status", table, type_="check")
        op.create_check_constraint("observation_status", table, _status_check(values))


def upgrade() -> None:
    _replace_status_check(STATUS_AFTER)


def downgrade() -> None:
    for table in OBSERVATION_TABLES:
        op.execute(f"DELETE FROM {table} WHERE status = 'deleted'")
    _replace_status_check(STATUS_BEFORE)
