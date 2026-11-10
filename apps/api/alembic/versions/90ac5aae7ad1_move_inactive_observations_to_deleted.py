"""남은 inactive 기록을 deleted 로 옮긴다 (#736)

기록 탭의 "잘못된 기록"(Correction wrong → inactive)을 기록 지우기로 바꿨다. 이미 그렇게 고친
기록도 지운 기록과 같게 둔다 (PM 결정 11-06). 이 revision 뒤로는 inactive 를 만드는 길이 없다 —
고치기 API 가 기록용 wrong 을 400 으로 막는다.

- status 를 deleted 로 바꾸고 검색 벡터(embedding)를 비운다. store 의 `delete_observation` 이 남기는
  모양과 같다. embedding 은 식사 · 교육 · 놀이 표에만 있다.
- 기억은 다시 계산하지 않는다. 기억 집계 · Curator 는 active 만 읽어서 inactive 가 deleted 가 돼도
  결과가 같다. Curator 보류 기록(observation_link_hold)은 wrong 으로 고칠 때 이미 지웠다.
- affinity_id 는 그대로 둔다(store 삭제도 남긴다). 기억에 묶이지 않은 행(NULL)도 같이 옮긴다.
- 고치기 이력(correction 의 wrong 행)은 그대로 둔다 — CHECK 도 그 값을 계속 허용한다.
- status CHECK 의 inactive 값은 남긴다. 값을 빼면 CHECK 교체라 expand-only 에 걸리고, 쓰는 행이
  없으면 해가 없다.

데이터만 바꾼다. 직전 코드로 롤백돼도 deleted 행은 원래 어디서도 보이지 않는다.
downgrade 는 하지 않는다 — 옮긴 행과 원래 지운 행을 가를 수 없다.

Revision ID: 90ac5aae7ad1
Revises: de2051c89680
Create Date: 2026-11-09
"""

from typing import Sequence, Union

from alembic import op

revision: str = "90ac5aae7ad1"
down_revision: Union[str, Sequence[str], None] = "de2051c89680"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 글자 그대로 적는다 — 나중에 모델이 바뀌어도 이 revision 은 움직이면 안 된다.
WITH_EMBEDDING = ("observation_food", "observation_education", "observation_activity")
WITHOUT_EMBEDDING = ("observation_routine", "observation_health")


def upgrade() -> None:
    for table in WITH_EMBEDDING:
        op.execute(
            f"UPDATE {table} SET status = 'deleted', embedding = NULL, updated_at = now() "
            "WHERE status = 'inactive'"
        )
    for table in WITHOUT_EMBEDDING:
        op.execute(
            f"UPDATE {table} SET status = 'deleted', updated_at = now() WHERE status = 'inactive'"
        )


def downgrade() -> None:
    pass
