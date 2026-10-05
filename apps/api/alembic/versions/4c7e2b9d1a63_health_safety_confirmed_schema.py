"""health_safety 를 10/4 확정안으로 바꾼다

- aliases 를 지운다. 매칭 폭은 allergen_terms.yaml 동의어표가 맡는다
- kind 에서 dietary_restriction 을 뺀다. 그 값의 행이 있으면 멈춘다 — 보호자가 넣은
  안전 정보를 마이그레이션이 다른 kind 로 옮기지 않는다
- category 를 text → varchar(32)[] 로. kind='allergy' 행만 값을 갖는다.
  기존 '식품'/'약물'/'환경' 은 food/drug/environment 로 옮기고, 그 밖의 값과
  알레르기가 아닌 행은 '{}' 가 된다
- state → status. 값은 active/retracted/none, 기본 active. 매칭되는 행이 없으면
  unknown 이라 unknown 은 저장하지 않는다
- management 를 jsonb → text. '{}' 는 NULL 로
- severity 에 class_0~class_6 을 더한다. kind='allergy' 는 Class 만, 나머지 kind 는
  mild~anaphylaxis 만 쓴다(CHECK health_safety_severity_by_kind). 알레르기 행에 옛 값이
  있으면 멈춘다 — Class 로 옮길 근거가 없고, 보호자가 넣은 값이라 지우지도 않는다

upgrade 에도 되돌릴 수 없는 손실이 있다 — 알레르기가 아닌 행의 category 와 세 값 밖의
자유 텍스트는 '{}' 가 되고, downgrade 로도 돌아오지 않는다.

downgrade 는 손실이 있다 — category 는 첫 값만 되돌리고, none 은 옛 값에 없어
retracted 로 내린다(둘 다 필터에서 거르지 않는 값이다). management 는 {"text": ...} 로
감싼다. class_* severity 는 NULL 이 된다. aliases 는 '{}' 로 되살아난다.

Revision ID: 4c7e2b9d1a63
Revises: 3e7a9c1d5b20
Create Date: 2026-10-05
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "4c7e2b9d1a63"
down_revision: Union[str, Sequence[str], None] = "3e7a9c1d5b20"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "health_safety"

KINDS_BEFORE = (
    "allergy",
    "chronic_disease",
    "dietary_restriction",
    "behavioral",
    "environmental",
    "other_medical",
)
KINDS_AFTER = ("allergy", "chronic_disease", "behavioral", "environmental", "other_medical")
STATE_BEFORE = ("active", "retracted")
STATUS_AFTER = ("active", "retracted", "none")
CATEGORY = {"식품": "food", "약물": "drug", "환경": "environment"}
SEVERITY_BEFORE = ("mild", "moderate", "severe", "anaphylaxis")
ALLERGY_SEVERITY = tuple(f"class_{n}" for n in range(7))
SEVERITY_AFTER = SEVERITY_BEFORE + ALLERGY_SEVERITY


def _in(column: str, values: tuple[str, ...]) -> str:
    joined = ", ".join(f"'{value}'" for value in values)
    return f"{column} IN ({joined})"


def _count(where: str) -> int:
    return (
        op.get_bind().execute(sa.text(f"SELECT count(*) FROM {TABLE} WHERE {where}")).scalar_one()
    )


def upgrade() -> None:
    # 바꾸기 전에 전부 검사한다. 하나라도 걸리면 아무것도 바꾸지 않고 멈춘다
    dietary = _count("kind = 'dietary_restriction'")
    if dietary:
        raise RuntimeError(
            f"{TABLE} 에 kind='dietary_restriction' 행이 {dietary}건 있다. 보호자가 넣은 "
            "안전 정보라 마이그레이션이 옮기지 않는다 — 직접 정리한 뒤 다시 올린다"
        )
    old_severity = _count("kind = 'allergy' AND severity IS NOT NULL")
    if old_severity:
        raise RuntimeError(
            f"{TABLE} 의 알레르기 행 {old_severity}건에 mild~anaphylaxis severity 가 있다. "
            "알레르기는 Class 0~6 만 쓰는데 옮길 근거가 없다 — 직접 정리한 뒤 다시 올린다"
        )

    op.drop_constraint("safety_kind", TABLE, type_="check")
    op.create_check_constraint("safety_kind", TABLE, _in("kind", KINDS_AFTER))

    op.drop_constraint("safety_severity", TABLE, type_="check")
    op.create_check_constraint("safety_severity", TABLE, _in("severity", SEVERITY_AFTER))
    allergy_severity = ", ".join(f"'{value}'" for value in ALLERGY_SEVERITY)
    op.create_check_constraint(
        "health_safety_severity_by_kind",
        TABLE,
        f"severity IS NULL OR (kind = 'allergy') = (severity IN ({allergy_severity}))",
    )

    op.drop_column(TABLE, "aliases")

    to_new = " ".join(f"WHEN category = '{ko}' THEN ARRAY['{en}']" for ko, en in CATEGORY.items())
    op.execute(
        f"""
        ALTER TABLE {TABLE} ALTER COLUMN category TYPE varchar(32)[] USING (
            CASE WHEN kind <> 'allergy' OR category IS NULL THEN '{{}}'
                 {to_new}
                 ELSE '{{}}' END
        )::varchar(32)[]
        """
    )
    op.alter_column(TABLE, "category", server_default=sa.text("'{}'"))
    values = ", ".join(f"'{en}'" for en in CATEGORY.values())
    op.create_check_constraint(
        "health_safety_category_values", TABLE, f"category <@ ARRAY[{values}]::varchar[]"
    )
    op.create_check_constraint(
        "health_safety_category_allergy_only",
        TABLE,
        "kind = 'allergy' OR coalesce(cardinality(category), 0) = 0",
    )

    op.drop_constraint("safety_state", TABLE, type_="check")
    op.alter_column(TABLE, "state", new_column_name="status")
    op.create_check_constraint("safety_status", TABLE, _in("status", STATUS_AFTER))

    # NOT NULL 을 먼저 푼다. '{}' 를 NULL 로 옮기는 USING 이 옛 제약에 걸린다
    op.alter_column(TABLE, "management", server_default=None, nullable=True)
    op.execute(
        f"""
        ALTER TABLE {TABLE} ALTER COLUMN management TYPE text
            USING CASE WHEN management = '{{}}'::jsonb THEN NULL ELSE management::text END
        """
    )


def downgrade() -> None:
    op.execute(
        f"""
        ALTER TABLE {TABLE} ALTER COLUMN management TYPE jsonb
            USING CASE WHEN management IS NULL THEN '{{}}'::jsonb
                       ELSE jsonb_build_object('text', management) END
        """
    )
    op.alter_column(TABLE, "management", server_default=sa.text("'{}'::jsonb"), nullable=False)

    op.drop_constraint("safety_status", TABLE, type_="check")
    op.execute(f"UPDATE {TABLE} SET status = 'retracted' WHERE status = 'none'")
    op.alter_column(TABLE, "status", new_column_name="state")
    op.create_check_constraint("safety_state", TABLE, _in("state", STATE_BEFORE))

    op.drop_constraint("health_safety_category_allergy_only", TABLE, type_="check")
    op.drop_constraint("health_safety_category_values", TABLE, type_="check")
    op.alter_column(TABLE, "category", server_default=None)
    to_old = " ".join(f"WHEN category[1] = '{en}' THEN '{ko}'" for ko, en in CATEGORY.items())
    op.execute(
        f"ALTER TABLE {TABLE} ALTER COLUMN category TYPE text USING (CASE {to_old} ELSE NULL END)"
    )

    op.add_column(
        TABLE,
        sa.Column("aliases", postgresql.ARRAY(sa.Text()), server_default="{}", nullable=False),
    )

    op.drop_constraint("health_safety_severity_by_kind", TABLE, type_="check")
    op.drop_constraint("safety_severity", TABLE, type_="check")
    op.execute(f"UPDATE {TABLE} SET severity = NULL WHERE {_in('severity', ALLERGY_SEVERITY)}")
    op.create_check_constraint("safety_severity", TABLE, _in("severity", SEVERITY_BEFORE))

    op.drop_constraint("safety_kind", TABLE, type_="check")
    op.create_check_constraint("safety_kind", TABLE, _in("kind", KINDS_BEFORE))
