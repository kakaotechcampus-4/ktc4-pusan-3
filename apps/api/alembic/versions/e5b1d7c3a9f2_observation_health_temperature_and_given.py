"""observation_health 에 체온 세 칸 · 먹인 약 두 칸을 더한다 (#557)

한 행 = 한 사건이다. 증상 행이거나 먹인 약 행이고, 약 행은 `symptom` 이 `'{}'` 이며
`observed_time` 이 먹인 시각이다. 같은 말에서 함께 기록된 증상 행은 약 행의
`linked_symptom_id` 가 가리킨다 — Memory 의 코드가 채운다.

🚨 배포 안전 — develop 머지는 `deploy-down` → `alembic upgrade heads` → `deploy-up` 이다.
   지금까지는 수정 인자(`ObservationHealthUpdate.symptom`)에 최소 1개 제한이 없어서 증상이 빈 행이
   있을 수 있다. 그런 행이 있는데 증상/약 CHECK 를 바로 걸면 업그레이드가 실패하고 서비스가 내려간
   채로 남는다. 그래서 그 CHECK 는 NOT VALID 로 건 뒤 증상이 빈 행을 세고, 0건이면 여기서
   VALIDATE 한다. 있으면 VALIDATE 하지 않고 경고만 남긴다 — 보호자 기록이라 지우지 않는다.
   NOT VALID 여도 새로 들어오거나 고치는 행은 검사한다.

🚨 CHECK 는 모델(app/domains/memory/observation/models.py)의 __table_args__ 와 같은 글자다.
   autogenerate 는 CHECK 를 비교하지 않는다.

downgrade 는 다섯 칸과 CHECK 를 지운다. 약 행은 증상이 빈 행으로 남는다(약 이름 손실).

Revision ID: e5b1d7c3a9f2
Revises: c6e2a8d41f93
Create Date: 2026-10-22
"""

import logging
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e5b1d7c3a9f2"
down_revision: Union[str, Sequence[str], None] = "c6e2a8d41f93"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

log = logging.getLogger("alembic.runtime.migration")

TABLE = "observation_health"
LINK_FK = "observation_health_linked_symptom_id_fkey"
SYMPTOM_OR_MEDICATION = "observation_health_symptom_or_medication"

# 마이그레이션은 앱 코드를 import 하지 않는다 — 나중에 모델이 바뀌어도 이 revision 은 그대로다.
CHECKS = {
    "observation_health_temperature_range": (
        "temperature IS NULL OR temperature BETWEEN 30.0 AND 45.0"
    ),
    "observation_health_measure_site": (
        "measure_site IN ('ear', 'forehead', 'armpit', 'oral', 'rectal')"
    ),
    "observation_health_link_needs_medication": (
        "linked_symptom_id IS NULL OR medication_given IS NOT NULL"
    ),
    # 약 행에는 체온이 없다. 다섯 칸이 모두 새 칸이라 기존 행에는 늘 참이다 — NOT VALID 가 필요 없다
    "observation_health_medication_no_temperature": (
        "medication_given IS NULL OR "
        "(temperature IS NULL AND measured_at IS NULL AND measure_site IS NULL)"
    ),
}
SYMPTOM_OR_MEDICATION_SQL = "(cardinality(symptom) > 0) <> (medication_given IS NOT NULL)"


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

    # 새 칸이라 기존 행의 medication_given 은 전부 NULL — 증상이 빈 행만 이 CHECK 에 걸린다
    op.execute(
        f"ALTER TABLE {TABLE} ADD CONSTRAINT {SYMPTOM_OR_MEDICATION} "
        f"CHECK ({SYMPTOM_OR_MEDICATION_SQL}) NOT VALID"
    )
    empty = (
        op.get_bind()
        .execute(sa.text(f"SELECT count(*) FROM {TABLE} WHERE cardinality(symptom) = 0"))
        .scalar_one()
    )
    if empty == 0:
        op.execute(f"ALTER TABLE {TABLE} VALIDATE CONSTRAINT {SYMPTOM_OR_MEDICATION}")
    else:
        # 건수만 남긴다 — 원문은 로그에 싣지 않는다(루트 CLAUDE.md §2)
        log.warning(
            "%s: 증상이 빈 기존 행 %d건 — %s 를 NOT VALID 로 둔다. 정리한 뒤 VALIDATE 한다",
            TABLE,
            empty,
            SYMPTOM_OR_MEDICATION,
        )


def downgrade() -> None:
    op.drop_constraint(SYMPTOM_OR_MEDICATION, TABLE, type_="check")
    for name in CHECKS:
        op.drop_constraint(name, TABLE, type_="check")
    op.alter_column(TABLE, "symptom", server_default=None)
    op.drop_constraint(LINK_FK, TABLE, type_="foreignkey")
    op.drop_column(TABLE, "linked_symptom_id")
    op.drop_column(TABLE, "medication_given")
    op.drop_column(TABLE, "measure_site")
    op.drop_column(TABLE, "measured_at")
    op.drop_column(TABLE, "temperature")
