"""polarity 값 막기 — 프로필 -1 · 1 · NULL, 관찰 -1 · 0 · 1 (#287)

`polarity = 0` 관찰은 좋다 · 싫다가 없는 기록이라 Curator 가 임베딩 · 연결하지 않는다.
CHECK 를 걸기 전에 걸리는 행을 정리한다.

- `0` 관찰의 `affinity_id` · `embedding` 을 비우고 그 관찰의 연결 보류 기록을 지운다
- `0` Profile 은 지운다. `NULL` 로 바꾸면 `pick_followup()` 이 되물을 거리로 집어서
  "아이스크림 좋아해요?" 같은 질문이 생긴다. 묶인 관찰은 남아 티어 3 근거로 쓰인다
- `confirmed` 인데 `polarity` 가 `NULL` 인 Profile 은 `candidate` 로 내린다 (지금 코드는 만들지 않는다)

🚨 downgrade 는 CHECK 만 푼다. 지운 Profile · 비운 칸은 되살리지 않는다.

Revision ID: 496fd5c10cd1
Revises: e4b19c07d2a6
Create Date: 2026-11-09 12:00:00.000000

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "496fd5c10cd1"
down_revision: Union[str, Sequence[str], None] = "e4b19c07d2a6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Profile 에 연결되는 세 표. routine 은 affinity_id · embedding 칸이 없다
PROMOTABLE = {
    "observation_food": "food",
    "observation_education": "education",
    "observation_activity": "activity",
}
OBSERVATION_TABLES = (*PROMOTABLE, "observation_routine")

# app/domains/memory/profile/models.py · observation/models.py 의 CHECK 와 같은 문장이다
PROFILE_POLARITY_CHECK = "polarity IS NULL OR polarity IN (-1, 1)"
PROFILE_CONFIRMED_POLARITY_CHECK = "state <> 'confirmed' OR polarity IS NOT NULL"
OBSERVATION_POLARITY_CHECK = "polarity IN (-1, 0, 1)"


def upgrade() -> None:
    for table, domain in PROMOTABLE.items():
        op.execute(
            f"DELETE FROM observation_link_hold WHERE domain = '{domain}'"
            f" AND observation_id IN (SELECT id FROM {table} WHERE polarity = 0)"
        )
        op.execute(
            f"UPDATE {table} SET affinity_id = NULL, embedding = NULL"
            " WHERE polarity = 0 AND (affinity_id IS NOT NULL OR embedding IS NOT NULL)"
        )
    op.execute("DELETE FROM profile_affinity WHERE NOT (" + PROFILE_POLARITY_CHECK + ")")
    op.execute(
        "UPDATE profile_affinity SET state = 'candidate'"
        " WHERE NOT (" + PROFILE_CONFIRMED_POLARITY_CHECK + ")"
    )

    op.create_check_constraint(
        "profile_affinity_polarity", "profile_affinity", PROFILE_POLARITY_CHECK
    )
    op.create_check_constraint(
        "profile_affinity_confirmed_polarity", "profile_affinity", PROFILE_CONFIRMED_POLARITY_CHECK
    )
    for table in OBSERVATION_TABLES:
        op.create_check_constraint("observation_polarity", table, OBSERVATION_POLARITY_CHECK)


def downgrade() -> None:
    for table in OBSERVATION_TABLES:
        op.drop_constraint("observation_polarity", table, type_="check")
    op.drop_constraint("profile_affinity_confirmed_polarity", "profile_affinity", type_="check")
    op.drop_constraint("profile_affinity_polarity", "profile_affinity", type_="check")
