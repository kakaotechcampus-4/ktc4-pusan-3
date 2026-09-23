"""보호자별 하루 입력 횟수 — app/api/quota.py

한 줄 입력 하나가 모델을 최대 9번쯤 부른다(Memory 7 + Supervisor 1~2). MLAPI 크레딧은 월 12만 원이고
넘으면 키가 자동 삭제된다 — run 당 상한만으로는 입력을 반복하는 것을 못 막아서 하루 횟수를 센다.
"""

import uuid
from datetime import date

import pytest

from app.api import quota

PARENT_A = uuid.UUID(int=1)
PARENT_B = uuid.UUID(int=2)
TODAY = date(2026, 9, 23)


@pytest.fixture(autouse=True)
def _clean_quota():
    quota.clear()
    yield
    quota.clear()


def test_allows_up_to_the_limit_then_refuses():
    results = [quota.consume(parent_id=PARENT_A, today=TODAY, limit=2) for _ in range(3)]

    assert results == [True, True, False]


def test_counts_each_parent_separately():
    """한 보호자가 한도에 걸려도 다른 보호자(같은 아이의 다른 보호자 포함)는 영향이 없다."""
    quota.consume(parent_id=PARENT_A, today=TODAY, limit=1)

    assert quota.consume(parent_id=PARENT_A, today=TODAY, limit=1) is False
    assert quota.consume(parent_id=PARENT_B, today=TODAY, limit=1) is True


def test_a_new_day_starts_from_zero():
    """날짜는 호출하는 쪽이 한국 시간으로 넘긴다 — 자정(KST)에 다시 0 부터."""
    quota.consume(parent_id=PARENT_A, today=TODAY, limit=1)

    assert quota.consume(parent_id=PARENT_A, today=date(2026, 9, 24), limit=1) is True
