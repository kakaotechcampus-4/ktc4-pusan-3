"""observation_health 에 체온 세 칸 · 먹인 약 두 칸을 더한다 (#551)

한 행 = 한 사건이다. 증상 행이거나 먹인 약 행이고, 약 행은 `symptom` 이 `'{}'` 이며
`observed_time` 이 먹인 시각이다. 같은 말에서 함께 기록된 증상 행은 약 행의
`linked_symptom_id` 가 가리킨다 — Memory 의 코드가 채운다.

🚨 배포 안전 — develop 머지는 `deploy-down` → `alembic upgrade heads` → `deploy-up` 이다.
   지금까지는 수정 인자(`ObservationHealthUpdate.symptom`)에 최소 1개 제한이 없어서 증상이 빈 행이
   있을 수 있다. DB 는 "증상과 약이 한 행에 같이 있다" 만 막는다(`symptom_not_with_medication`) —
   기존 행은 약 칸이 전부 NULL 이라 늘 참이고, 그래서 바로 VALIDATE 된다.
   "증상도 약도 없다" 는 DB 에서 막지 않는다. 막으면 증상이 빈 옛 행이 그 CHECK 에 걸려 이후
   UPDATE(soft delete · Correction 포함)가 전부 실패한다. 새 행은 Memory 스키마가 막는다
   (`ObservationHealthCreate` 의 증상 또는 약 · `ObservationHealthUpdate.symptom` 최소 1개).

🚨 CHECK 는 모델(app/domains/memory/observation/models.py)의 __table_args__ 와 같은 글자다.
   autogenerate 는 CHECK 를 비교하지 않는다.

downgrade 는 다섯 칸과 CHECK 를 지운다. 약 행은 증상이 빈 행으로 남는다(약 이름 손실).

Revision ID: e5b1d7c3a9f2
Revises: c6e2a8d41f93
Create Date: 2026-10-22
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e5b1d7c3a9f2"
down_revision: Union[str, Sequence[str], None] = "c6e2a8d41f93"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "observation_health"
LINK_FK = "observation_health_linked_symptom_id_fkey"

# 마이그레이션은 앱 코드를 import 하지 않는다 — 나중에 모델이 바뀌어도 이 revision 은 그대로다.
# 다섯 칸이 모두 새 칸이라 기존 행에는 늘 참이다 — NOT VALID 가 필요 없다
CHECKS = {
    "observation_health_temperature_range": (
        "temperature IS NULL OR temperature BETWEEN 30.0 AND 45.0"
    ),
    "observation_health_measure_site": (
        "measure_site IN ('ear', 'forehead', 'armpit', 'oral', 'rectal')"
    ),
    # 한 행 = 한 사건. 증상 행이거나 약 행이다 — 둘이 한 행에 같이 있지 않다
    "observation_health_symptom_not_with_medication": (
        "cardinality(symptom) = 0 OR medication_given IS NULL"
    ),
    "observation_health_link_needs_medication": (
        "linked_symptom_id IS NULL OR medication_given IS NOT NULL"
    ),
    # 약 행에는 체온이 없다
    "observation_health_medication_no_temperature": (
        "medication_given IS NULL OR "
        "(temperature IS NULL AND measured_at IS NULL AND measure_site IS NULL)"
    ),
}


def upgrade() -> None:
    op.add_column(TABLE, sa.Column("temperature", sa.Numeric(3, 1), nullable=True))
    op.add_column(TABLE, sa.Column("measured_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(TABLE, sa.Column("measure_site", sa.String(16), nullable=True))
    op.add_column(TABLE, sa.Column("medication_given", sa.Text(), nullable=True))
    op.add_column(TABLE, sa.Column("linked_symptom_id", sa.UUID(), nullable=True))
    op.create_foreign_key(LINK_FK, TABLE, TABLE, ["linked_symptom_id"], ["id"], ondelete="SET NULL")
    # 약 행은 symptom 을 넘기지 않는다
    op.alter_column(TABLE, "symptom", server_default="{}")
    for name, condition in CHECKS.items():
        op.create_check_constraint(name, TABLE, condition)


def downgrade() -> None:
    for name in CHECKS:
        op.drop_constraint(name, TABLE, type_="check")
    op.alter_column(TABLE, "symptom", server_default=None)
    op.drop_constraint(LINK_FK, TABLE, type_="foreignkey")
    op.drop_column(TABLE, "linked_symptom_id")
    op.drop_column(TABLE, "medication_given")
    op.drop_column(TABLE, "measure_site")
    op.drop_column(TABLE, "measured_at")
    op.drop_column(TABLE, "temperature")
