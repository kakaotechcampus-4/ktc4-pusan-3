"""장소 표 place — 공원 · 어린이놀이시설 · 도서관을 한 표에 두고 source 로 가른다 (#516)

설계는 docs/agents/activity/activity-agent-v1.md D9 "place 적재 규칙". 공용 데이터라 child_id 가
없다. 스키마만 맡는다 — 행은 scripts/load_places.py 가 수동으로 싣는다(CSV 를 커밋하지 않는다).

- 화이트리스트를 칸으로 강제한다. 전화번호 · 관리기관 · 주소 칸이 없다.
- 기본 키 (source, source_id) 가 곧 UNIQUE 이고 적재가 여기에 upsert 한다. 장소 행은 근거가 아니라
  다른 표가 가리키지 않아서 대리 키를 두지 않는다. 그 밖의 인덱스는 없다 — 반경 검색이 인덱스 없이
  실측 5~6ms 라, 실제 데이터로 EXPLAIN 을 본 뒤 단다.

CHECK 로 막는 것
  - source 는 Activity 포트의 세 값 · category 는 PlaceCategory 다섯 값
  - sigungu_code 는 비었거나 숫자 5자리
  - 좌표는 한국 상자 안(적재 스크립트가 거르는 상자와 같다) — 위경도가 뒤바뀐 행을 막는다

autogenerate 를 쓰지 않고 손으로 썼다. 적용 뒤 `alembic revision --autogenerate` 가 빈 diff 를
내는지로 모델과 맞는지 확인한다.

Revision ID: 5bdc0aad2e70
Revises: 091b165cedea
Create Date: 2026-10-27
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "5bdc0aad2e70"
down_revision: Union[str, Sequence[str], None] = "091b165cedea"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# app/domains/place/models.py 와 같은 글자다
SOURCE = "source IN ('city_park', 'kids_play_facility', 'library')"
CATEGORY = "category IN ('park', 'playground', 'library', 'indoor_playground', 'experience_center')"
SIGUNGU = "sigungu_code IS NULL OR sigungu_code ~ '^[0-9]{5}$'"
KOREA_BOX = "lat BETWEEN 33 AND 39 AND lng BETWEEN 124 AND 132"


def upgrade() -> None:
    op.create_table(
        "place",
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("source_id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("category", sa.String(20), nullable=False),
        sa.Column("lat", sa.Double(), nullable=False),
        sa.Column("lng", sa.Double(), nullable=False),
        sa.Column("sigungu_code", sa.String(5), nullable=True),
        sa.Column("data_date", sa.Date(), nullable=True),
        sa.Column("source_version", sa.String(32), nullable=True),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("source", "source_id"),
        sa.CheckConstraint(SOURCE, name="place_source"),
        sa.CheckConstraint(CATEGORY, name="place_category"),
        sa.CheckConstraint(SIGUNGU, name="place_sigungu_code"),
        sa.CheckConstraint(KOREA_BOX, name="place_korea_box"),
    )


def downgrade() -> None:
    op.drop_table("place")
